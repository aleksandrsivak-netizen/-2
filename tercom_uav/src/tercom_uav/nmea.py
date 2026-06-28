"""NMEA-0183 GPGGA parser and generator for radio-altimeter payloads."""

from __future__ import annotations

from pathlib import Path

from tercom_uav.types import GGARecord


class NMEAError(ValueError):
    """Raised when an NMEA sentence cannot be parsed or validated."""


def compute_checksum(payload: str) -> str:
    """Compute NMEA checksum for the payload between `$` and `*`."""

    checksum = 0
    for char in payload:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def add_checksum(payload: str) -> str:
    """Return a full NMEA sentence with `$` prefix and checksum suffix."""

    return f"${payload}*{compute_checksum(payload)}"


def _split_sentence(line: str) -> tuple[str, str | None, bool]:
    stripped = line.strip()
    if not stripped:
        raise NMEAError("Empty NMEA sentence.")
    if stripped.startswith("$"):
        stripped = stripped[1:]
    if "*" not in stripped:
        return stripped, None, False
    payload, checksum = stripped.rsplit("*", 1)
    checksum = checksum.strip().upper()
    if len(checksum) < 2:
        raise NMEAError("Malformed NMEA checksum field.")
    expected = compute_checksum(payload)
    return payload, checksum[:2], expected == checksum[:2]


def parse_utc_seconds(value: str) -> float | None:
    """Parse GGA UTC time `hhmmss.sss` into seconds from midnight."""

    if not value:
        return None
    try:
        hours = int(value[0:2])
        minutes = int(value[2:4])
        seconds = float(value[4:])
    except (ValueError, IndexError) as exc:
        raise NMEAError(f"Invalid UTC time field: {value!r}") from exc
    return hours * 3600.0 + minutes * 60.0 + seconds


def _parse_optional_float(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise NMEAError(f"Invalid floating-point field: {value!r}") from exc


def _parse_optional_int(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise NMEAError(f"Invalid integer field: {value!r}") from exc


def _parse_coordinate(value: str, hemisphere: str, degree_digits: int) -> float | None:
    if value == "" and hemisphere == "":
        return None
    if value == "" or hemisphere == "":
        raise NMEAError("Incomplete latitude/longitude fields.")
    try:
        degrees = int(value[:degree_digits])
        minutes = float(value[degree_digits:])
    except (ValueError, IndexError) as exc:
        raise NMEAError(f"Invalid coordinate field: {value!r}") from exc
    coordinate = float(degrees) + minutes / 60.0
    hemi = hemisphere.upper()
    if hemi in {"S", "W"}:
        coordinate = -coordinate
    elif hemi not in {"N", "E"}:
        raise NMEAError(f"Invalid coordinate hemisphere: {hemisphere!r}")
    return coordinate


def _format_coordinate(value: float | None, is_latitude: bool) -> tuple[str, str]:
    if value is None:
        return "", ""
    coordinate = float(value)
    if is_latitude:
        if not -90.0 <= coordinate <= 90.0:
            raise ValueError("Latitude must be in range [-90, 90].")
        hemisphere = "N" if coordinate >= 0.0 else "S"
        degree_width = 2
    else:
        if not -180.0 <= coordinate <= 180.0:
            raise ValueError("Longitude must be in range [-180, 180].")
        hemisphere = "E" if coordinate >= 0.0 else "W"
        degree_width = 3
    absolute = abs(coordinate)
    degrees = int(absolute)
    minutes = (absolute - degrees) * 60.0
    return f"{degrees:0{degree_width}d}{minutes:07.4f}", hemisphere


def parse_gpgga(line: str, require_checksum: bool = True) -> GGARecord:
    """Parse a GPGGA/GNGGA sentence.

    The altitude field at index 9 is interpreted as radio altitude above
    ground level by the task specification. Latitude and longitude fields are
    not used for navigation truth.
    """

    payload, checksum, checksum_valid = _split_sentence(line)
    if require_checksum and checksum is not None and not checksum_valid:
        raise NMEAError("Invalid NMEA checksum.")
    if require_checksum and checksum is None:
        raise NMEAError("NMEA checksum is required.")

    fields = payload.split(",")
    if len(fields) < 10:
        raise NMEAError("GPGGA sentence has too few fields.")
    sentence_type = fields[0].upper()
    if sentence_type not in {"GPGGA", "GNGGA"}:
        raise NMEAError(f"Unsupported NMEA sentence type: {sentence_type}.")

    utc_seconds = parse_utc_seconds(fields[1])
    lat_deg = _parse_coordinate(fields[2], fields[3], 2) if len(fields) > 3 else None
    lon_deg = _parse_coordinate(fields[4], fields[5], 3) if len(fields) > 5 else None
    quality = _parse_optional_int(fields[6]) if len(fields) > 6 else None
    satellites = _parse_optional_int(fields[7]) if len(fields) > 7 else None
    hdop = _parse_optional_float(fields[8]) if len(fields) > 8 else None
    radio_alt_m = _parse_optional_float(fields[9])

    return GGARecord(
        raw=line.strip(),
        utc_seconds=utc_seconds,
        radio_alt_m=radio_alt_m,
        checksum=checksum,
        checksum_valid=checksum_valid,
        quality=quality,
        satellites=satellites,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        hdop=hdop,
    )


def format_utc_time(seconds_from_start: float, start_hour: int = 12) -> str:
    """Format scenario-relative seconds as NMEA `hhmmss.ss` UTC time."""

    total = (start_hour * 3600.0 + seconds_from_start) % 86400.0
    hours = int(total // 3600)
    minutes = int((total - hours * 3600) // 60)
    seconds = total - hours * 3600 - minutes * 60
    return f"{hours:02d}{minutes:02d}{seconds:05.2f}"


def generate_gpgga(
    radio_alt_agl_m: float,
    time_s: float,
    fix_quality: int = 1,
    lat_deg: float | None = None,
    lon_deg: float | None = None,
    satellites: int = 8,
    hdop: float = 1.0,
) -> str:
    """Generate a GPGGA sentence carrying radio altitude in the altitude field."""

    utc = format_utc_time(time_s)
    lat_field, ns = _format_coordinate(lat_deg, is_latitude=True)
    lon_field, ew = _format_coordinate(lon_deg, is_latitude=False)
    payload = (
        f"GPGGA,{utc},{lat_field},{ns},{lon_field},{ew},"
        f"{fix_quality},{int(satellites):02d},{float(hdop):.1f},{radio_alt_agl_m:.2f},M,0.0,M,,"
    )
    return add_checksum(payload)


def read_gpgga_file(path: str | Path, require_checksum: bool = True) -> list[GGARecord]:
    """Read and parse all non-empty GPGGA records from a text file."""

    return read_gpgga_text(Path(path).read_text(encoding="utf-8"), source=str(path), require_checksum=require_checksum)


def read_gpgga_text(text: str, source: str = "<text>", require_checksum: bool = True) -> list[GGARecord]:
    """Read and parse all non-empty GPGGA records from text."""

    records: list[GGARecord] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            records.append(parse_gpgga(stripped, require_checksum=require_checksum))
        except NMEAError as exc:
            raise NMEAError(f"{source}:{line_no}: {exc}") from exc
    return records

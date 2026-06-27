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
    quality = _parse_optional_int(fields[6]) if len(fields) > 6 else None
    satellites = _parse_optional_int(fields[7]) if len(fields) > 7 else None
    radio_alt_m = _parse_optional_float(fields[9])

    return GGARecord(
        raw=line.strip(),
        utc_seconds=utc_seconds,
        radio_alt_m=radio_alt_m,
        checksum=checksum,
        checksum_valid=checksum_valid,
        quality=quality,
        satellites=satellites,
    )


def format_utc_time(seconds_from_start: float, start_hour: int = 12) -> str:
    """Format scenario-relative seconds as NMEA `hhmmss.ss` UTC time."""

    total = (start_hour * 3600.0 + seconds_from_start) % 86400.0
    hours = int(total // 3600)
    minutes = int((total - hours * 3600) // 60)
    seconds = total - hours * 3600 - minutes * 60
    return f"{hours:02d}{minutes:02d}{seconds:05.2f}"


def generate_gpgga(radio_alt_agl_m: float, time_s: float, fix_quality: int = 1) -> str:
    """Generate a GPGGA sentence carrying radio altitude in the altitude field."""

    utc = format_utc_time(time_s)
    payload = f"GPGGA,{utc},,,,,{fix_quality},08,1.0,{radio_alt_agl_m:.2f},M,0.0,M,,"
    return add_checksum(payload)


def read_gpgga_file(path: str | Path, require_checksum: bool = True) -> list[GGARecord]:
    """Read and parse all non-empty GPGGA records from a text file."""

    records: list[GGARecord] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            records.append(parse_gpgga(stripped, require_checksum=require_checksum))
        except NMEAError as exc:
            raise NMEAError(f"{path}:{line_no}: {exc}") from exc
    return records

"""Minimal NMEA-0183 GGA support for radio altimeter streams."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedNMEA:
    timestamp_s: float | None
    altitude_m: float | None
    raw: str
    checksum_ok: bool
    parsed_ok: bool
    error: str | None = None


def compute_nmea_checksum(sentence_without_dollar_and_checksum: str) -> str:
    """Compute an uppercase two-digit NMEA XOR checksum."""

    checksum = 0
    for char in sentence_without_dollar_and_checksum:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def build_gpgga_sentence(timestamp_seconds: float, altitude_m: float) -> str:
    """Build a GPGGA-like sentence with empty coordinates and valid checksum."""

    timestamp = _format_nmea_time(timestamp_seconds)
    body = f"GPGGA,{timestamp},,,,,,,,{float(altitude_m):.1f},M,0.0,M,,"
    checksum = compute_nmea_checksum(body)
    return f"${body}*{checksum}"


def parse_gpgga_sentence(sentence: str) -> ParsedNMEA:
    """Parse a GPGGA or GNGGA sentence without raising on malformed input."""

    raw = sentence.rstrip("\r\n")
    text = raw.strip()
    if not text:
        return ParsedNMEA(None, None, raw, False, False, "empty sentence")
    if not text.startswith("$"):
        return ParsedNMEA(None, None, raw, False, False, "sentence must start with '$'")
    if "*" not in text:
        return ParsedNMEA(None, None, raw, False, False, "missing checksum separator '*'")

    body, provided_checksum = text[1:].rsplit("*", 1)
    provided_checksum = provided_checksum.strip().upper()
    expected_checksum = compute_nmea_checksum(body)
    checksum_ok = provided_checksum == expected_checksum
    if not checksum_ok:
        return ParsedNMEA(None, None, raw, False, False, "checksum mismatch")

    try:
        fields = body.split(",")
        if not fields or not fields[0].endswith("GGA"):
            return ParsedNMEA(None, None, raw, True, False, "not a GGA sentence")
        if len(fields) <= 9:
            return ParsedNMEA(None, None, raw, True, False, "missing altitude field")
        timestamp_s = _parse_nmea_time(fields[1]) if fields[1] else None
        if fields[9] == "":
            return ParsedNMEA(timestamp_s, None, raw, True, False, "empty altitude field")
        altitude_m = float(fields[9])
        return ParsedNMEA(timestamp_s, altitude_m, raw, True, True, None)
    except Exception as exc:
        return ParsedNMEA(None, None, raw, True, False, str(exc))


def parse_nmea_text(text: str) -> list[ParsedNMEA]:
    """Parse all non-empty lines from an NMEA text blob."""

    return [parse_gpgga_sentence(line) for line in text.splitlines() if line.strip()]


def _format_nmea_time(timestamp_seconds: float) -> str:
    seconds_in_day = float(timestamp_seconds) % 86400.0
    hours = int(seconds_in_day // 3600)
    minutes = int((seconds_in_day % 3600) // 60)
    seconds = seconds_in_day - hours * 3600 - minutes * 60
    return f"{hours:02d}{minutes:02d}{seconds:06.3f}"


def _parse_nmea_time(value: str) -> float:
    if len(value) < 6:
        raise ValueError("invalid timestamp")
    hours = int(value[0:2])
    minutes = int(value[2:4])
    seconds = float(value[4:])
    if hours > 23 or minutes > 59 or seconds >= 60.0:
        raise ValueError("timestamp out of range")
    return hours * 3600.0 + minutes * 60.0 + seconds

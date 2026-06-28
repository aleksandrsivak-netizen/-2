"""GPS health checks and GPS-assisted TERCOM fusion."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Callable, Iterable, Any

from tercom_uav.config import GPSFusionConfig
from tercom_uav.types import GGARecord, NavigationEstimate


class NavigationMode(str, Enum):
    GPS_OFF_TERCOM_ONLY = "GPS_OFF_TERCOM_ONLY"
    GPS_HEALTHY_ASSISTED = "GPS_HEALTHY_ASSISTED"
    GPS_DEGRADED = "GPS_DEGRADED"
    GPS_REJECTED_REACQUIRE = "GPS_REJECTED_REACQUIRE"
    DATA_STALE = "DATA_STALE"
    DATA_INVALID = "DATA_INVALID"


@dataclass(slots=True)
class GPSFix:
    """One GPS position fix converted to the local TERCOM map frame."""

    timestamp_s: float | None = None
    receive_time_s: float | None = None
    x_m: float | None = None
    y_m: float | None = None
    lat_deg: float | None = None
    lon_deg: float | None = None
    speed_mps: float | None = None
    course_deg: float | None = None
    hdop: float | None = None
    pdop: float | None = None
    satellites: int | None = None
    fix_quality: int | None = None
    checksum_valid: bool | None = None
    raw: str | None = None

    @property
    def has_position(self) -> bool:
        return _finite(self.x_m) and _finite(self.y_m)


@dataclass(slots=True)
class InputMeasurementDiagnostic:
    source: str
    timestamp_s: float | None
    receive_time_s: float | None
    age_ms: float | None
    is_stale: bool
    is_out_of_order: bool
    quality: float | None
    accepted: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "timestamp": self.timestamp_s,
            "receive_time": self.receive_time_s,
            "age_ms": self.age_ms,
            "is_stale": self.is_stale,
            "is_out_of_order": self.is_out_of_order,
            "quality": self.quality,
            "accepted": self.accepted,
            "reason": self.reason,
        }


@dataclass(slots=True)
class GPSEvaluation:
    enabled: bool
    healthy: bool
    accepted: bool
    mode: NavigationMode
    quality_score: float
    age_ms: float | None
    reject_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    good_count: int = 0
    bad_count: int = 0
    input_diagnostic: InputMeasurementDiagnostic | None = None

    def to_dict(self, fix: GPSFix | None = None) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "healthy": self.healthy,
            "accepted": self.accepted,
            "reject_reason": self.reject_reason,
            "quality": self.quality_score,
            "age_ms": self.age_ms,
            "hdop": None if fix is None else fix.hdop,
            "pdop": None if fix is None else fix.pdop,
            "satellites": None if fix is None else fix.satellites,
            "fix_quality": None if fix is None else fix.fix_quality,
            "good_count": self.good_count,
            "bad_count": self.bad_count,
        }


@dataclass(slots=True)
class FusionResult:
    estimate: NavigationEstimate
    diagnostics: dict[str, Any]


class GPSFusionState:
    """Stateful GPS debounce/hysteresis across consecutive fixes."""

    def __init__(self) -> None:
        self.good_count = 0
        self.bad_count = 0
        self.mode = NavigationMode.GPS_OFF_TERCOM_ONLY
        self.last_fix: GPSFix | None = None
        self.last_accepted_fix: GPSFix | None = None
        self.last_timestamp_s: float | None = None

    def reset_after_rejection(self) -> None:
        self.last_accepted_fix = None

    def evaluate(
        self,
        fix: GPSFix | None,
        config: GPSFusionConfig | None = None,
        tercom_estimate: NavigationEstimate | None = None,
    ) -> GPSEvaluation:
        cfg = config or GPSFusionConfig()
        cfg.validate()

        if fix is None or not fix.has_position:
            self.mode = NavigationMode.GPS_OFF_TERCOM_ONLY
            diagnostic = InputMeasurementDiagnostic(
                source="gps",
                timestamp_s=None if fix is None else fix.timestamp_s,
                receive_time_s=None if fix is None else fix.receive_time_s,
                age_ms=None,
                is_stale=False,
                is_out_of_order=False,
                quality=None,
                accepted=False,
                reason="gps_coordinates_missing",
            )
            return GPSEvaluation(
                enabled=False,
                healthy=False,
                accepted=False,
                mode=self.mode,
                quality_score=0.0,
                age_ms=None,
                reject_reason="gps_coordinates_missing",
                good_count=self.good_count,
                bad_count=self.bad_count,
                input_diagnostic=diagnostic,
            )

        age = _age_ms(fix)
        is_stale = age is not None and age > cfg.stale_data_timeout_ms
        is_late = age is not None and age > cfg.gps_max_age_ms
        is_out_of_order = (
            fix.timestamp_s is not None
            and self.last_timestamp_s is not None
            and fix.timestamp_s < self.last_timestamp_s
        )
        reasons: list[str] = []
        hard_reject = False
        stale_reject = False
        invalid_reject = False

        if fix.checksum_valid is False:
            reasons.append("nmea_checksum_invalid")
            hard_reject = True
        if fix.fix_quality is not None and fix.fix_quality <= 0:
            reasons.append("fix_quality_invalid")
            hard_reject = True
        if fix.satellites is not None and fix.satellites < cfg.gps_min_satellites:
            reasons.append("satellites_below_threshold")
        if fix.hdop is not None and fix.hdop > cfg.gps_max_hdop:
            reasons.append("hdop_above_threshold")
        if is_late:
            reasons.append("gps_age_above_nominal")
        if is_stale:
            stale_reject = True
            reasons.append("gps_data_stale")
        if is_out_of_order:
            invalid_reject = True
            reasons.append("gps_data_out_of_order")

        if self.last_accepted_fix and self.last_accepted_fix.has_position:
            distance = _distance_m(fix, self.last_accepted_fix)
            dt = _time_delta_s(fix, self.last_accepted_fix)
            if distance > cfg.gps_max_position_jump_m:
                reasons.append("position_jump_exceeds_limit")
                hard_reject = True
            if dt is not None and dt > 0.0:
                implied_speed = distance / dt
                if implied_speed > cfg.max_uav_speed_mps:
                    reasons.append("speed_exceeds_limit")
                    hard_reject = True
        if fix.speed_mps is not None and fix.speed_mps > cfg.max_uav_speed_mps:
            reasons.append("speed_exceeds_limit")
            hard_reject = True

        if tercom_estimate is not None:
            disagreement = math.hypot(fix.x_m - tercom_estimate.x_m, fix.y_m - tercom_estimate.y_m)
            if (
                tercom_estimate.confidence_score >= cfg.gps_tercom_high_confidence
                and disagreement > cfg.gps_tercom_max_disagreement_m
            ):
                reasons.append("gps_tercom_disagreement")
                hard_reject = True
            elif disagreement > cfg.gps_tercom_max_disagreement_m:
                reasons.append("gps_tercom_disagreement_degraded")

        quality = _quality_score(fix, age, cfg, reasons)
        reason = reasons[0] if reasons else None

        if hard_reject or stale_reject or invalid_reject:
            self.bad_count += 1
            self.good_count = 0
            if hard_reject:
                self.mode = NavigationMode.GPS_REJECTED_REACQUIRE
                self.reset_after_rejection()
            elif stale_reject:
                self.mode = NavigationMode.DATA_STALE
            else:
                self.mode = NavigationMode.DATA_INVALID
            accepted = False
            healthy = False
        elif reasons:
            self.bad_count += 1
            self.good_count = 0
            self.mode = NavigationMode.GPS_DEGRADED
            accepted = True
            healthy = False
        else:
            self.good_count += 1
            self.bad_count = 0
            if self.mode == NavigationMode.GPS_REJECTED_REACQUIRE and self.good_count < cfg.gps_good_required_count:
                accepted = False
                healthy = False
                reason = "awaiting_gps_reacquire_hysteresis"
            else:
                self.mode = NavigationMode.GPS_HEALTHY_ASSISTED
                accepted = True
                healthy = True

        if fix.timestamp_s is not None:
            self.last_timestamp_s = fix.timestamp_s
        self.last_fix = fix
        if accepted:
            self.last_accepted_fix = fix

        diagnostic = InputMeasurementDiagnostic(
            source="gps",
            timestamp_s=fix.timestamp_s,
            receive_time_s=fix.receive_time_s,
            age_ms=age,
            is_stale=is_stale,
            is_out_of_order=is_out_of_order,
            quality=quality,
            accepted=accepted,
            reason=reason,
        )
        warnings = [_warning_for_reason(item) for item in reasons]
        warnings = [item for item in warnings if item]
        return GPSEvaluation(
            enabled=True,
            healthy=healthy,
            accepted=accepted,
            mode=self.mode,
            quality_score=quality,
            age_ms=age,
            reject_reason=None if accepted else reason,
            warnings=warnings,
            good_count=self.good_count,
            bad_count=self.bad_count,
            input_diagnostic=diagnostic,
        )


def gga_records_to_gps_fixes(
    records: Iterable[GGARecord],
    local_converter: Callable[[float, float], tuple[float, float]] | None = None,
) -> list[GPSFix]:
    """Convert parsed GGA records to GPS fixes.

    `local_converter` receives `(lat_deg, lon_deg)` and returns local
    `(x_m, y_m)`. If it is omitted or a record has no coordinates, the fix is
    still returned, but `has_position` will be false and GPS-assisted mode will
    stay disabled.
    """

    fixes: list[GPSFix] = []
    for record in records:
        x_m = y_m = None
        if record.lat_deg is not None and record.lon_deg is not None and local_converter is not None:
            try:
                x_m, y_m = local_converter(record.lat_deg, record.lon_deg)
            except Exception:
                x_m = y_m = None
        fixes.append(
            GPSFix(
                timestamp_s=record.utc_seconds,
                receive_time_s=record.utc_seconds,
                x_m=x_m,
                y_m=y_m,
                lat_deg=record.lat_deg,
                lon_deg=record.lon_deg,
                hdop=record.hdop,
                satellites=record.satellites,
                fix_quality=record.quality,
                checksum_valid=record.checksum_valid,
                raw=record.raw,
            )
        )
    return fixes


def first_usable_gps_anchor(
    fixes: Iterable[GPSFix],
    config: GPSFusionConfig | None = None,
) -> GPSFix | None:
    """Return the first fix suitable for a GPS-assisted search window."""

    cfg = config or GPSFusionConfig()
    cfg.validate()
    for fix in fixes:
        if not fix.has_position:
            continue
        if fix.checksum_valid is False:
            continue
        if fix.fix_quality is not None and fix.fix_quality <= 0:
            continue
        if fix.satellites is not None and fix.satellites < cfg.gps_min_satellites:
            continue
        if fix.hdop is not None and fix.hdop > cfg.gps_max_hdop:
            continue
        age = _age_ms(fix)
        if age is not None and age > cfg.stale_data_timeout_ms:
            continue
        return fix
    return None


def fuse_navigation_estimate(
    tercom_estimate: NavigationEstimate,
    gps_fix: GPSFix | None,
    config: GPSFusionConfig | None = None,
    state: GPSFusionState | None = None,
    search_window_m: float | None = None,
) -> FusionResult:
    """Fuse a TERCOM estimate with one GPS fix and return diagnostics."""

    cfg = config or GPSFusionConfig()
    cfg.validate()
    fusion_state = state or GPSFusionState()
    evaluation = fusion_state.evaluate(gps_fix, cfg, tercom_estimate=tercom_estimate)
    tercom_conf = _clip(tercom_estimate.confidence_score, 0.0, 1.0)

    gps_weight = 0.0
    tercom_weight = 1.0
    disagreement = None
    fused = _copy_estimate(tercom_estimate)
    warnings = list(evaluation.warnings)

    if gps_fix is not None and gps_fix.has_position:
        disagreement = math.hypot(gps_fix.x_m - tercom_estimate.x_m, gps_fix.y_m - tercom_estimate.y_m)

    if evaluation.accepted and gps_fix is not None and gps_fix.has_position:
        gps_quality = _clip(evaluation.quality_score, 0.0, 1.0)
        if evaluation.mode == NavigationMode.GPS_DEGRADED:
            gps_quality = min(gps_quality, cfg.gps_degraded_max_weight)
        total = max(gps_quality + tercom_conf, 1e-9)
        gps_weight = gps_quality / total
        if evaluation.mode == NavigationMode.GPS_HEALTHY_ASSISTED:
            gps_weight = _clip(gps_weight, cfg.gps_healthy_weight_min, cfg.gps_healthy_weight_max)
        else:
            gps_weight = _clip(gps_weight, 0.0, cfg.gps_degraded_max_weight)
        tercom_weight = 1.0 - gps_weight
        fused.x_m = float(tercom_weight * tercom_estimate.x_m + gps_weight * gps_fix.x_m)
        fused.y_m = float(tercom_weight * tercom_estimate.y_m + gps_weight * gps_fix.y_m)
        if gps_fix.speed_mps is not None and _finite(gps_fix.speed_mps):
            fused.speed_mps = float(tercom_weight * tercom_estimate.speed_mps + gps_weight * gps_fix.speed_mps)
        if gps_fix.course_deg is not None and _finite(gps_fix.course_deg):
            fused.azimuth_deg = _fuse_angle_deg(tercom_estimate.azimuth_deg, gps_fix.course_deg, gps_weight)
        azimuth_rad = math.radians(fused.azimuth_deg)
        fused.vx_mps = math.sin(azimuth_rad) * fused.speed_mps
        fused.vy_mps = math.cos(azimuth_rad) * fused.speed_mps
        agreement_boost = 0.0
        if disagreement is not None and disagreement <= cfg.gps_tercom_max_disagreement_m:
            agreement_boost = 0.08 * (1.0 - disagreement / cfg.gps_tercom_max_disagreement_m)
        fused.confidence_score = _clip(
            tercom_weight * tercom_conf + gps_weight * gps_quality + agreement_boost,
            0.0,
            1.0,
        )
    elif evaluation.mode in {NavigationMode.GPS_REJECTED_REACQUIRE, NavigationMode.DATA_STALE, NavigationMode.DATA_INVALID}:
        fused.confidence_score = _clip(tercom_conf * 0.65, 0.0, 1.0)
        if evaluation.mode == NavigationMode.GPS_REJECTED_REACQUIRE:
            warnings.append("TERCOM reinitialization started")

    diagnostics = {
        "mode": evaluation.mode.value,
        "position": {
            "x_m": fused.x_m,
            "y_m": fused.y_m,
            "lat": None if gps_fix is None else gps_fix.lat_deg,
            "lon": None if gps_fix is None else gps_fix.lon_deg,
        },
        "confidence": fused.confidence_score,
        "gps": evaluation.to_dict(gps_fix),
        "tercom": {
            "confidence": tercom_conf,
            "matched": not tercom_estimate.ambiguous_match,
            "search_window_m": search_window_m,
            "reacquire": evaluation.mode == NavigationMode.GPS_REJECTED_REACQUIRE,
        },
        "fusion": {
            "gps_weight": gps_weight,
            "tercom_weight": tercom_weight,
            "disagreement_m": disagreement,
        },
        "inputs": [] if evaluation.input_diagnostic is None else [evaluation.input_diagnostic.to_dict()],
        "warnings": warnings,
    }
    return FusionResult(estimate=fused, diagnostics=diagnostics)


def tercom_only_diagnostics(estimate: NavigationEstimate, search_window_m: float | None = None) -> dict[str, Any]:
    """Build diagnostics for the legacy GPS-off path."""

    return {
        "mode": NavigationMode.GPS_OFF_TERCOM_ONLY.value,
        "position": {"x_m": estimate.x_m, "y_m": estimate.y_m, "lat": None, "lon": None},
        "confidence": estimate.confidence_score,
        "gps": {
            "enabled": False,
            "healthy": False,
            "accepted": False,
            "reject_reason": "gps_coordinates_missing",
            "quality": 0.0,
            "age_ms": None,
            "hdop": None,
            "pdop": None,
            "satellites": None,
            "fix_quality": None,
            "good_count": 0,
            "bad_count": 0,
        },
        "tercom": {
            "confidence": estimate.confidence_score,
            "matched": not estimate.ambiguous_match,
            "search_window_m": search_window_m,
            "reacquire": False,
        },
        "fusion": {"gps_weight": 0.0, "tercom_weight": 1.0, "disagreement_m": None},
        "inputs": [],
        "warnings": [],
    }


def _age_ms(fix: GPSFix) -> float | None:
    if fix.timestamp_s is None or fix.receive_time_s is None:
        return None
    return max(0.0, (float(fix.receive_time_s) - float(fix.timestamp_s)) * 1000.0)


def _copy_estimate(estimate: NavigationEstimate) -> NavigationEstimate:
    return NavigationEstimate(
        time_s=estimate.time_s,
        x_m=estimate.x_m,
        y_m=estimate.y_m,
        azimuth_deg=estimate.azimuth_deg,
        speed_mps=estimate.speed_mps,
        vx_mps=estimate.vx_mps,
        vy_mps=estimate.vy_mps,
        traveled_distance_m=estimate.traveled_distance_m,
        confidence_score=estimate.confidence_score,
        ambiguous_match=estimate.ambiguous_match,
        dead_reckoning=estimate.dead_reckoning,
    )


def _quality_score(fix: GPSFix, age_ms: float | None, cfg: GPSFusionConfig, reasons: list[str]) -> float:
    score = 1.0
    if fix.hdop is not None:
        score *= _clip(cfg.gps_max_hdop / max(float(fix.hdop), 1e-9), 0.15, 1.0)
    if fix.satellites is not None and cfg.gps_min_satellites > 0:
        score *= _clip(float(fix.satellites) / float(cfg.gps_min_satellites + 3), 0.2, 1.0)
    if age_ms is not None and age_ms > cfg.gps_max_age_ms:
        score *= _clip(cfg.gps_max_age_ms / max(age_ms, 1e-9), 0.15, 1.0)
    if fix.fix_quality is not None and fix.fix_quality <= 0:
        score = 0.0
    if reasons:
        score = min(score, 0.55)
    return _clip(score, 0.0, 1.0)


def _distance_m(a: GPSFix, b: GPSFix) -> float:
    return math.hypot(float(a.x_m) - float(b.x_m), float(a.y_m) - float(b.y_m))


def _time_delta_s(a: GPSFix, b: GPSFix) -> float | None:
    if a.timestamp_s is not None and b.timestamp_s is not None:
        return max(0.0, float(a.timestamp_s) - float(b.timestamp_s))
    if a.receive_time_s is not None and b.receive_time_s is not None:
        return max(0.0, float(a.receive_time_s) - float(b.receive_time_s))
    return None


def _fuse_angle_deg(tercom_deg: float, gps_deg: float, gps_weight: float) -> float:
    delta = (float(gps_deg) - float(tercom_deg) + 180.0) % 360.0 - 180.0
    return (float(tercom_deg) + gps_weight * delta) % 360.0


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _warning_for_reason(reason: str) -> str | None:
    messages = {
        "position_jump_exceeds_limit": "GPS rejected because position jump is physically impossible",
        "speed_exceeds_limit": "GPS rejected because speed is physically impossible",
        "gps_tercom_disagreement": "GPS rejected because it diverges from a confident TERCOM estimate",
        "gps_data_stale": "GPS data is stale",
        "gps_data_out_of_order": "GPS data arrived out of order",
        "hdop_above_threshold": "GPS HDOP is above threshold",
        "satellites_below_threshold": "GPS satellite count is below threshold",
        "nmea_checksum_invalid": "GPS NMEA checksum is invalid",
        "fix_quality_invalid": "GPS fix quality is invalid",
    }
    return messages.get(reason)

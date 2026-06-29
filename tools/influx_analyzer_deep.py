#!/usr/bin/env python3
"""InfluxDB Deep Analyzer v2.

Deep analysis for time-series telemetry:
- seasonal baseline by hour-of-week
- counter unwrap + rate analysis
- changepoint detection (rolling median shift)
- stale/missing data checks
- prioritized incident scoring

Supports external InfluxDB v2 endpoints via CLI args or env vars.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_DEVICE_TAGS = ["agent_host", "host", "device", "source", "instance"]
DEFAULT_COUNTER_HINTS = [
    "octets",
    "pkts",
    "packets",
    "counter",
    "corrected",
    "uncorrectable",
    "replied",
    "unreplied",
    "sent",
    "received",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deep anomaly and error analysis from InfluxDB time-series data",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=os.getenv("INFLUX_URL"), help="InfluxDB URL")
    parser.add_argument("--org", default=os.getenv("INFLUX_ORG"), help="InfluxDB organization")
    parser.add_argument("--token", default=os.getenv("INFLUX_TOKEN"), help="InfluxDB token")
    parser.add_argument("--bucket", default=os.getenv("INFLUX_BUCKET"), help="InfluxDB bucket")
    parser.add_argument("--lookback", default="14d", help="Flux range lookback, e.g. 48h, 14d")
    parser.add_argument("--measurements", default="", help="Comma-separated measurements")
    parser.add_argument("--fields", default="", help="Comma-separated fields")
    parser.add_argument(
        "--device-tags",
        default=",".join(DEFAULT_DEVICE_TAGS),
        help="Comma-separated ordered list of tags used as device id",
    )
    parser.add_argument("--counter-fields", default="", help="Comma-separated explicit counter fields")
    parser.add_argument("--min-points", type=int, default=36, help="Minimum points for deep analysis")
    parser.add_argument(
        "--seasonal-z-threshold",
        type=float,
        default=3.0,
        help="Absolute seasonal robust z-score threshold",
    )
    parser.add_argument(
        "--changepoint-sigma",
        type=float,
        default=3.0,
        help="Median-shift threshold in MAD units",
    )
    parser.add_argument(
        "--stale-multiplier",
        type=float,
        default=3.0,
        help="Series considered stale if age > stale-multiplier * median interval",
    )
    parser.add_argument("--max-series", type=int, default=0, help="0 = analyze all series")
    parser.add_argument("--top", type=int, default=30, help="Top incident count in console summary")
    parser.add_argument("--output-json", default="", help="Optional JSON report file path")

    args = parser.parse_args()
    missing = [
        label
        for label, value in [
            ("--url / INFLUX_URL", args.url),
            ("--org / INFLUX_ORG", args.org),
            ("--token / INFLUX_TOKEN", args.token),
            ("--bucket / INFLUX_BUCKET", args.bucket),
        ]
        if not value
    ]
    if missing:
        parser.error("Missing required connection settings: " + ", ".join(missing))
    return args


def split_csv(value: str) -> List[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def flux_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_flux(bucket: str, lookback: str, measurements: Sequence[str], fields: Sequence[str]) -> str:
    clauses: List[str] = []
    if measurements:
        m = " or ".join([f'r._measurement == "{flux_escape(x)}"' for x in measurements])
        clauses.append(f"({m})")
    if fields:
        f = " or ".join([f'r._field == "{flux_escape(x)}"' for x in fields])
        clauses.append(f"({f})")

    filter_step = ""
    if clauses:
        filter_step = "\n  |> filter(fn: (r) => " + " and ".join(clauses) + ")"

    return (
        f'from(bucket: "{flux_escape(bucket)}")\n'
        f"  |> range(start: -{lookback})"
        f"{filter_step}\n"
        "  |> keep(columns: [\"_time\",\"_measurement\",\"_field\",\"_value\",\"agent_host\",\"host\",\"device\",\"source\",\"instance\"])"
    )


def influx_query(url: str, org: str, token: str, query: str) -> str:
    endpoint = url.rstrip("/") + "/api/v2/query?org=" + urllib.parse.quote(org)
    req = urllib.request.Request(endpoint, method="POST")
    req.add_header("Authorization", f"Token {token}")
    req.add_header("Accept", "application/csv")
    req.add_header("Content-Type", "application/vnd.flux")

    try:
        with urllib.request.urlopen(req, data=query.encode("utf-8"), timeout=90) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"InfluxDB query failed: HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"InfluxDB query failed: {exc}") from exc


def parse_time(value: str) -> dt.datetime:
    if value.endswith("Z"):
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.datetime.fromisoformat(value)


def parse_csv(csv_text: str) -> List[Dict[str, str]]:
    lines = [ln for ln in csv_text.splitlines() if ln and not ln.startswith("#")]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    out: List[Dict[str, str]] = []
    for row in reader:
        if row.get("_value") in (None, ""):
            continue
        out.append(row)
    return out


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    s = sorted(values)
    idx = (len(s) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(s[lo])
    w = idx - lo
    return float(s[lo] * (1.0 - w) + s[hi] * w)


def mad(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def robust_z(value: float, center: float, scale_mad: float) -> float:
    if scale_mad <= 0:
        return 0.0
    return 0.6745 * (value - center) / scale_mad


def infer_device(row: Dict[str, str], device_tags: Sequence[str]) -> str:
    for tag in device_tags:
        val = row.get(tag)
        if val:
            return val
    return "unknown"


def is_counter(field: str, explicit: Sequence[str]) -> bool:
    if field in explicit:
        return True
    low = field.lower()
    return any(h in low for h in DEFAULT_COUNTER_HINTS)


def median_interval_seconds(times: Sequence[dt.datetime]) -> Optional[float]:
    if len(times) < 3:
        return None
    diffs = [
        (times[i] - times[i - 1]).total_seconds()
        for i in range(1, len(times))
        if (times[i] - times[i - 1]).total_seconds() > 0
    ]
    if not diffs:
        return None
    return statistics.median(diffs)


def hour_of_week(ts: dt.datetime) -> int:
    return ts.weekday() * 24 + ts.hour


def seasonal_baseline(points: Sequence[Tuple[dt.datetime, float]], latest_ts: dt.datetime) -> Tuple[Optional[float], Optional[float], int]:
    target_how = hour_of_week(latest_ts)
    target_hour = latest_ts.hour

    candidates: List[float] = []
    for ts, value in points:
        if ts >= latest_ts:
            continue
        # Keep same hour-of-week, plus one-hour neighborhood for robustness.
        how = hour_of_week(ts)
        if how == target_how or abs(ts.hour - target_hour) <= 1:
            candidates.append(value)

    if len(candidates) < 8:
        return None, None, len(candidates)

    base_med = statistics.median(candidates)
    base_mad = mad(candidates)
    return base_med, base_mad, len(candidates)


def unwrap_counter(values: Sequence[float]) -> Tuple[List[float], int, int]:
    """Return unwrapped values + reset count + wrap count."""
    if not values:
        return [], 0, 0

    out: List[float] = []
    offset = 0.0
    resets = 0
    wraps = 0
    prev_raw = values[0]
    prev_unwrapped = values[0]
    out.append(prev_unwrapped)

    for raw in values[1:]:
        candidate = raw + offset
        if candidate < prev_unwrapped:
            # Try 32-bit wrap.
            if prev_raw > 4_000_000_000 and raw < 1_000_000_000:
                offset += 2**32
                wraps += 1
                candidate = raw + offset
            # Try 64-bit wrap.
            elif prev_raw > 1.8e19 and raw < 1e18:
                offset += 2**64
                wraps += 1
                candidate = raw + offset
            else:
                # Treat as reset/restart.
                offset += prev_unwrapped
                resets += 1
                candidate = raw + offset

        out.append(candidate)
        prev_raw = raw
        prev_unwrapped = candidate

    return out, resets, wraps


def rolling_median_shift(values: Sequence[float], sigma: float) -> Tuple[bool, float, int]:
    if len(values) < 40:
        return False, 0.0, 0

    w = max(12, min(72, len(values) // 6))
    recent = list(values[-w:])
    prev = list(values[-2 * w : -w])
    if len(prev) < w:
        return False, 0.0, w

    med_prev = statistics.median(prev)
    med_recent = statistics.median(recent)
    pool_mad = mad(prev + recent)

    if pool_mad <= 0:
        if med_prev == 0:
            return False, 0.0, w
        rel = abs((med_recent - med_prev) / med_prev)
        return rel > 0.5, rel, w

    shift_score = abs(med_recent - med_prev) / pool_mad
    return shift_score >= sigma, shift_score, w


def analyze_non_counter(
    points: Sequence[Tuple[dt.datetime, float]],
    seasonal_threshold: float,
    changepoint_sigma: float,
    stale_multiplier: float,
) -> Dict[str, object]:
    times = [t for t, _ in points]
    values = [v for _, v in points]
    latest_ts = times[-1]
    latest_val = values[-1]

    reasons: List[str] = []
    score = 0

    med = statistics.median(values)
    m = mad(values)
    rz = robust_z(latest_val, med, m)

    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    if latest_val < lower or latest_val > upper:
        reasons.append("outside_iqr_bounds")
        score += 1

    base_med, base_mad, base_n = seasonal_baseline(points, latest_ts)
    seasonal_rz = None
    if base_med is not None and base_mad is not None:
        seasonal_rz = robust_z(latest_val, base_med, base_mad)
        if abs(seasonal_rz) >= seasonal_threshold:
            reasons.append("seasonal_deviation")
            score += 3

    cp, cp_score, cp_w = rolling_median_shift(values, changepoint_sigma)
    if cp:
        reasons.append("changepoint_detected")
        score += 3

    interval_s = median_interval_seconds(times)
    stale = False
    age_s = None
    if interval_s and interval_s > 0:
        age_s = max(0.0, (dt.datetime.now(dt.timezone.utc) - latest_ts).total_seconds())
        if age_s > stale_multiplier * interval_s:
            stale = True
            reasons.append("stale_data")
            score += 2

    # Catch common hard fault pattern.
    if latest_val == 0 and med > 0:
        reasons.append("hard_drop_to_zero")
        score += 4

    return {
        "latest": latest_val,
        "latest_time": latest_ts.isoformat(),
        "median": med,
        "mad": m,
        "robust_z": rz,
        "iqr_lower": lower,
        "iqr_upper": upper,
        "seasonal_baseline_median": base_med,
        "seasonal_baseline_mad": base_mad,
        "seasonal_sample_count": base_n,
        "seasonal_robust_z": seasonal_rz,
        "changepoint_score": cp_score,
        "changepoint_window": cp_w,
        "median_interval_seconds": interval_s,
        "latest_age_seconds": age_s,
        "stale": stale,
        "reasons": reasons,
        "incident_score": score,
        "anomaly": score > 0,
    }


def analyze_counter(
    points: Sequence[Tuple[dt.datetime, float]],
    seasonal_threshold: float,
    stale_multiplier: float,
) -> Dict[str, object]:
    times = [t for t, _ in points]
    raw_vals = [v for _, v in points]
    latest_ts = times[-1]

    reasons: List[str] = []
    score = 0

    unwrapped, resets, wraps = unwrap_counter(raw_vals)

    rates: List[float] = []
    for i in range(1, len(unwrapped)):
        dt_s = (times[i] - times[i - 1]).total_seconds()
        if dt_s <= 0:
            continue
        inc = unwrapped[i] - unwrapped[i - 1]
        rates.append(max(0.0, inc / dt_s))

    latest_rate = rates[-1] if rates else 0.0

    if resets > 0:
        reasons.append("counter_reset_detected")
        # Lower weight than hard non-counter deviation.
        score += min(4, 1 + resets // 10)

    if wraps > 0:
        reasons.append("counter_wrap_detected")

    if len(rates) >= 20:
        med_r = statistics.median(rates)
        mad_r = mad(rates)
        rz_r = robust_z(latest_rate, med_r, mad_r)

        q1 = percentile(rates, 0.25)
        q3 = percentile(rates, 0.75)
        iqr = q3 - q1
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr

        if latest_rate < low or latest_rate > high:
            reasons.append("rate_outside_iqr_bounds")
            score += 1

        # Seasonal baseline on rates.
        rate_points = list(zip(times[1:], rates))
        base_med, base_mad, base_n = seasonal_baseline(rate_points, latest_ts)
        seasonal_rz = None
        if base_med is not None and base_mad is not None:
            seasonal_rz = robust_z(latest_rate, base_med, base_mad)
            if abs(seasonal_rz) >= seasonal_threshold:
                reasons.append("rate_seasonal_deviation")
                score += 2
        else:
            base_n = 0
            seasonal_rz = None
    else:
        med_r = None
        mad_r = None
        rz_r = None
        low = None
        high = None
        seasonal_rz = None
        base_n = 0

    # Stuck counter rate: recent increments all zero while historical had activity.
    if len(rates) >= 24:
        recent = rates[-12:]
        hist = rates[:-12]
        if hist and max(recent) == 0 and statistics.median(hist) > 0:
            reasons.append("counter_stuck")
            score += 3

    interval_s = median_interval_seconds(times)
    age_s = None
    stale = False
    if interval_s and interval_s > 0:
        age_s = max(0.0, (dt.datetime.now(dt.timezone.utc) - latest_ts).total_seconds())
        if age_s > stale_multiplier * interval_s:
            stale = True
            reasons.append("stale_data")
            score += 2

    return {
        "latest": raw_vals[-1],
        "latest_time": latest_ts.isoformat(),
        "unwrapped_latest": unwrapped[-1],
        "counter_resets": resets,
        "counter_wraps": wraps,
        "latest_rate": latest_rate,
        "rate_median": med_r,
        "rate_mad": mad_r,
        "rate_robust_z": rz_r,
        "rate_iqr_lower": low,
        "rate_iqr_upper": high,
        "rate_seasonal_robust_z": seasonal_rz,
        "seasonal_sample_count": base_n,
        "median_interval_seconds": interval_s,
        "latest_age_seconds": age_s,
        "stale": stale,
        "reasons": reasons,
        "incident_score": score,
        "anomaly": score > 0,
    }


def severity_from_score(score: int) -> str:
    if score >= 7:
        return "critical"
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    if score >= 1:
        return "low"
    return "none"


def main() -> int:
    args = parse_args()

    measurements = split_csv(args.measurements)
    fields = split_csv(args.fields)
    device_tags = split_csv(args.device_tags)
    counter_fields = split_csv(args.counter_fields)

    flux_query = build_flux(args.bucket, args.lookback, measurements, fields)
    csv_text = influx_query(args.url, args.org, args.token, flux_query)
    rows = parse_csv(csv_text)

    if not rows:
        print("No data returned. Check bucket/lookback/filters.")
        return 0

    series_map: Dict[Tuple[str, str, str], List[Tuple[dt.datetime, float]]] = defaultdict(list)

    for row in rows:
        time_raw = row.get("_time")
        val_raw = row.get("_value")
        if not time_raw or val_raw is None:
            continue

        try:
            ts = parse_time(time_raw)
            val = float(val_raw)
        except ValueError:
            continue

        measurement = row.get("_measurement") or "unknown"
        field = row.get("_field") or "unknown"
        device = infer_device(row, device_tags)
        series_map[(measurement, field, device)].append((ts, val))

    keys = list(series_map.keys())
    if args.max_series > 0:
        keys = keys[: args.max_series]

    incidents: List[Dict[str, object]] = []
    too_few = 0

    for measurement, field, device in keys:
        points = sorted(series_map[(measurement, field, device)], key=lambda x: x[0])
        if len(points) < args.min_points:
            too_few += 1
            continue

        counter_mode = is_counter(field, counter_fields)
        if counter_mode:
            analysis = analyze_counter(
                points,
                seasonal_threshold=args.seasonal_z_threshold,
                stale_multiplier=args.stale_multiplier,
            )
        else:
            analysis = analyze_non_counter(
                points,
                seasonal_threshold=args.seasonal_z_threshold,
                changepoint_sigma=args.changepoint_sigma,
                stale_multiplier=args.stale_multiplier,
            )

        score = int(analysis.get("incident_score", 0))
        if score <= 0:
            continue

        incident = {
            "measurement": measurement,
            "field": field,
            "device": device,
            "counter_mode": counter_mode,
            "severity": severity_from_score(score),
            **analysis,
        }
        incidents.append(incident)

    incidents.sort(key=lambda x: (int(x.get("incident_score", 0)), str(x.get("severity", ""))), reverse=True)

    by_severity = defaultdict(int)
    for item in incidents:
        by_severity[str(item.get("severity", "unknown"))] += 1

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "analyzer": "influx_analyzer_deep_v2",
        "influx": {
            "url": args.url,
            "org": args.org,
            "bucket": args.bucket,
            "lookback": args.lookback,
        },
        "query": {
            "measurements": measurements,
            "fields": fields,
            "device_tags": device_tags,
            "counter_fields": counter_fields,
        },
        "summary": {
            "rows_read": len(rows),
            "series_total": len(keys),
            "series_with_too_few_points": too_few,
            "incidents": len(incidents),
            "by_severity": dict(by_severity),
        },
        "incidents": incidents,
    }

    print("InfluxDB Deep Analyzer v2")
    print("=========================")
    print(f"Rows read:       {report['summary']['rows_read']}")
    print(f"Series total:    {report['summary']['series_total']}")
    print(f"Too few points:  {report['summary']['series_with_too_few_points']}")
    print(f"Incidents:       {report['summary']['incidents']}")
    print("By severity:")
    for sev in ["critical", "high", "medium", "low"]:
        print(f"  {sev:8s} {report['summary']['by_severity'].get(sev, 0)}")

    if incidents:
        print(f"\nTop incidents (max {args.top}):")
        for item in incidents[: args.top]:
            print(
                "- {sev} | {dev} | {meas}.{fld} | score={sc} | reasons={reasons}".format(
                    sev=item.get("severity"),
                    dev=item.get("device"),
                    meas=item.get("measurement"),
                    fld=item.get("field"),
                    sc=item.get("incident_score"),
                    reasons=",".join(item.get("reasons", [])),
                )
            )

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nFull report written: {args.output_json}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

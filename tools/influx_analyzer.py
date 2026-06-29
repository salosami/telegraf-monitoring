#!/usr/bin/env python3
"""Analyze InfluxDB time-series data for anomalies and counter issues.

This script supports external InfluxDB instances by taking connection
settings from CLI arguments or environment variables.
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
from typing import Dict, Iterable, List, Sequence, Tuple


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
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze InfluxDB data for anomalies and errors.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default=os.getenv("INFLUX_URL"), help="InfluxDB URL")
    parser.add_argument("--org", default=os.getenv("INFLUX_ORG"), help="InfluxDB organization")
    parser.add_argument("--token", default=os.getenv("INFLUX_TOKEN"), help="InfluxDB token")
    parser.add_argument("--bucket", default=os.getenv("INFLUX_BUCKET"), help="InfluxDB bucket")
    parser.add_argument("--lookback", default="24h", help="Flux range lookback window, e.g. 24h, 7d")
    parser.add_argument(
        "--measurements",
        default="",
        help="Comma-separated measurement filters (empty = all)",
    )
    parser.add_argument(
        "--fields",
        default="",
        help="Comma-separated field filters (empty = all)",
    )
    parser.add_argument(
        "--device-tags",
        default=",".join(DEFAULT_DEVICE_TAGS),
        help="Comma-separated ordered list of tag names used as device id",
    )
    parser.add_argument(
        "--counter-fields",
        default="",
        help="Comma-separated field names that are counters",
    )
    parser.add_argument(
        "--min-points",
        type=int,
        default=12,
        help="Minimum points required per series for anomaly analysis",
    )
    parser.add_argument(
        "--robust-z-threshold",
        type=float,
        default=3.5,
        help="Absolute robust z-score threshold for anomalies",
    )
    parser.add_argument(
        "--spike-sigma",
        type=float,
        default=4.0,
        help="Std-dev multiplier for counter increment spike detection",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional output path for full JSON report",
    )
    parser.add_argument(
        "--max-series",
        type=int,
        default=0,
        help="Optional cap for number of analyzed series (0 = no cap)",
    )
    args = parser.parse_args()

    missing = [
        name
        for name, value in [
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


def csv_list(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def flux_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_flux(bucket: str, lookback: str, measurements: Sequence[str], fields: Sequence[str]) -> str:
    clauses = []
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


def query_influx(url: str, org: str, token: str, flux_query: str) -> str:
    endpoint = url.rstrip("/") + "/api/v2/query?org=" + urllib.parse.quote(org)
    req = urllib.request.Request(endpoint, method="POST")
    req.add_header("Authorization", f"Token {token}")
    req.add_header("Accept", "application/csv")
    req.add_header("Content-Type", "application/vnd.flux")
    payload = flux_query.encode("utf-8")

    try:
        with urllib.request.urlopen(req, data=payload, timeout=60) as resp:
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


def parse_csv_rows(csv_text: str) -> List[Dict[str, str]]:
    lines = [line for line in csv_text.splitlines() if line and not line.startswith("#")]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    rows: List[Dict[str, str]] = []
    for row in reader:
        if row.get("_value") in (None, ""):
            continue
        rows.append(row)
    return rows


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    s = sorted(values)
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return float(d0 + d1)


def choose_device(row: Dict[str, str], device_tags: Sequence[str]) -> str:
    for tag in device_tags:
        v = row.get(tag)
        if v:
            return v
    return "unknown"


def is_counter_field(field: str, explicit_counter_fields: Sequence[str]) -> bool:
    if field in explicit_counter_fields:
        return True
    low = field.lower()
    return any(hint in low for hint in DEFAULT_COUNTER_HINTS)


def analyze_series(
    series_points: List[Tuple[dt.datetime, float]],
    robust_threshold: float,
    min_points: int,
    spike_sigma: float,
    counter_mode: bool,
) -> Dict[str, object]:
    points = sorted(series_points, key=lambda x: x[0])
    values = [p[1] for p in points]
    latest = values[-1]

    result: Dict[str, object] = {
        "count": len(values),
        "latest": latest,
        "latest_time": points[-1][0].isoformat(),
        "anomaly": False,
        "reasons": [],
    }

    if len(values) < min_points:
        result["reasons"].append("too_few_points")
        return result

    med = statistics.median(values)
    abs_dev = [abs(v - med) for v in values]
    mad = statistics.median(abs_dev)

    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    robust_z = 0.0
    if mad > 0:
        robust_z = 0.6745 * (latest - med) / mad

    out_of_iqr = latest < lower or latest > upper
    out_of_robust = abs(robust_z) >= robust_threshold if mad > 0 else False

    result.update(
        {
            "median": med,
            "mad": mad,
            "q1": q1,
            "q3": q3,
            "iqr_lower": lower,
            "iqr_upper": upper,
            "robust_z": robust_z,
        }
    )

    if out_of_iqr:
        result["reasons"].append("outside_iqr_bounds")
    if out_of_robust:
        result["reasons"].append("robust_z_threshold")

    if counter_mode:
        diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
        negative_diffs = [d for d in diffs if d < 0]
        non_negative = [d for d in diffs if d >= 0]
        latest_diff = diffs[-1] if diffs else 0.0

        result["counter_resets"] = len(negative_diffs)
        result["latest_increment"] = latest_diff

        if negative_diffs:
            result["reasons"].append("counter_reset_detected")

        if len(non_negative) >= 3:
            inc_med = statistics.median(non_negative)
            inc_std = statistics.pstdev(non_negative)
            spike_limit = inc_med + spike_sigma * inc_std if inc_std > 0 else float("inf")
            result["increment_median"] = inc_med
            result["increment_std"] = inc_std
            result["increment_spike_limit"] = spike_limit
            if latest_diff > spike_limit and spike_limit != float("inf"):
                result["reasons"].append("counter_increment_spike")

    result["anomaly"] = len(result["reasons"]) > 0
    return result


def main() -> int:
    args = parse_args()
    measurements = csv_list(args.measurements)
    fields = csv_list(args.fields)
    device_tags = csv_list(args.device_tags)
    counter_fields = csv_list(args.counter_fields)

    flux_query = build_flux(args.bucket, args.lookback, measurements, fields)
    csv_text = query_influx(args.url, args.org, args.token, flux_query)
    rows = parse_csv_rows(csv_text)

    if not rows:
        print("No rows returned for the selected query.")
        return 0

    series: Dict[Tuple[str, str, str], List[Tuple[dt.datetime, float]]] = defaultdict(list)

    for row in rows:
        try:
            value = float(row.get("_value", ""))
        except ValueError:
            continue

        measurement = row.get("_measurement", "") or "unknown"
        field = row.get("_field", "") or "unknown"
        time_value = row.get("_time", "")
        if not time_value:
            continue

        try:
            ts = parse_time(time_value)
        except ValueError:
            continue

        device = choose_device(row, device_tags)
        key = (measurement, field, device)
        series[key].append((ts, value))

    keys = list(series.keys())
    if args.max_series > 0:
        keys = keys[: args.max_series]

    analyzed = []
    for measurement, field, device in keys:
        counter_mode = is_counter_field(field, counter_fields)
        result = analyze_series(
            series[(measurement, field, device)],
            robust_threshold=args.robust_z_threshold,
            min_points=args.min_points,
            spike_sigma=args.spike_sigma,
            counter_mode=counter_mode,
        )
        result.update(
            {
                "measurement": measurement,
                "field": field,
                "device": device,
                "counter_mode": counter_mode,
            }
        )
        analyzed.append(result)

    anomalies = [r for r in analyzed if r.get("anomaly")]
    critical = [
        r
        for r in anomalies
        if "counter_reset_detected" in r.get("reasons", [])
        or "robust_z_threshold" in r.get("reasons", [])
    ]

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
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
            "series_analyzed": len(analyzed),
            "anomalies": len(anomalies),
            "critical": len(critical),
        },
        "anomalies": anomalies,
    }

    print("InfluxDB Analyzer Report")
    print("========================")
    print(f"Rows read:         {report['summary']['rows_read']}")
    print(f"Series analyzed:   {report['summary']['series_analyzed']}")
    print(f"Anomalies found:   {report['summary']['anomalies']}")
    print(f"Critical findings: {report['summary']['critical']}")

    if anomalies:
        print("\nTop anomalies (max 20):")
        for item in anomalies[:20]:
            reasons = ",".join(item.get("reasons", []))
            latest = item.get("latest")
            rz = item.get("robust_z")
            rz_text = f"{rz:.2f}" if isinstance(rz, (int, float)) else "n/a"
            print(
                f"- {item['device']} | {item['measurement']}.{item['field']} | "
                f"latest={latest} | robust_z={rz_text} | reasons={reasons}"
            )

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nFull JSON report written to: {args.output_json}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

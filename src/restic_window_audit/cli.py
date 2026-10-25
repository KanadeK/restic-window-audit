import argparse
import json
from pathlib import Path

from .core import InputError, audit, read_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit retained restic recovery-point time coverage offline"
    )
    parser.add_argument("snapshots", type=Path, help="Ungrouped restic snapshots --json export")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-json-bytes", type=int, default=64 * 1024**2)
    parser.add_argument("--max-snapshots", type=int, default=100_000)
    parser.add_argument(
        "--max-gaps", type=int, default=200, help="Reported gaps per workload; totals remain exact"
    )
    args = parser.parse_args(argv)
    try:
        report = audit(
            read_json(args.snapshots, args.max_json_bytes),
            read_json(args.policy, args.max_json_bytes),
            max_snapshots=args.max_snapshots,
            max_gaps=args.max_gaps,
        )
        code = 0 if report["status"] == "pass" else 1
    except (InputError, OSError) as exc:
        report = {"schema_version": 1, "status": "error", "error": str(exc)}
        code = 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    elif code == 2:
        print(f"error: {report['error']}")
    else:
        print("Retained recovery-point coverage (not job uptime or data integrity):")
        for item in report["workloads"]:
            print(
                f"{item['status'].upper()} {item['name']!r}: {item['uncovered_seconds']}s uncovered; {item['gap_count']} gaps"
            )
            for gap in item["gaps"]:
                print(f"  {gap['start']} -> {gap['end']} ({gap['duration_seconds']}s)")
            if item["omitted_gaps"]:
                print(f"  ... {item['omitted_gaps']} additional gaps omitted")
    return code

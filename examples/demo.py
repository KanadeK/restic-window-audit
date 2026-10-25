"""Demonstrate historical gaps hidden by a recent snapshot; no restic required."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

contract = json.loads((Path(__file__).parent / "policy.json").read_text(encoding="utf-8"))


def point(hour):
    return {
        "id": f"{hour + 1:064x}",
        "time": f"2026-01-01T{hour:02}:00:00Z",
        "hostname": "demo-server",
        "paths": ["/srv/app"],
        "tags": ["daily"],
    }


with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)
    policy = root / "policy.json"
    data = root / "snapshots.json"
    policy.write_text(json.dumps(contract), encoding="utf-8")
    for hours, expected in [([0, 3], 1), ([0, 1, 2, 3], 0), ([], 1)]:
        data.write_text(json.dumps([point(h) for h in hours]), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "restic_window_audit",
                str(data),
                "--policy",
                str(policy),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != expected:
            raise RuntimeError(result.stdout + result.stderr)
        report = json.loads(result.stdout)
        if hours == [0, 3] and report["workloads"][0]["uncovered_seconds"] != "7200":
            raise RuntimeError("Incorrect historical gap")
        print(
            f"hours={hours}: {report['status']}, uncovered={report['workloads'][0]['uncovered_seconds']}s"
        )
    print(
        "PASS: historical gap, continuous coverage and missing workload tested; scratch cleaned up"
    )

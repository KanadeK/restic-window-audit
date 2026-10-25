from __future__ import annotations

import contextlib
import copy
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from restic_window_audit.cli import main
from restic_window_audit.core import (
    InputError,
    audit,
    coverage,
    format_timestamp,
    read_json,
    timestamp,
)


def snapshot(hour, i=1, **kwargs):
    return {
        "id": f"{i:064x}",
        "time": f"2026-01-01T{hour:02}:00:00Z",
        "hostname": "server",
        "paths": ["/data"],
        **kwargs,
    }


def contract():
    return {
        "version": 1,
        "since": "2026-01-01T00:00:00Z",
        "until": "2026-01-01T04:00:00Z",
        "workloads": [
            {"name": "app", "hostname": "server", "paths": ["/data"], "max_age_seconds": 3600}
        ],
    }


class CoverageTests(unittest.TestCase):
    def test_continuous(self):
        report = audit([snapshot(h, h + 1) for h in range(4)], contract())
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["workloads"][0]["covered_seconds"], "14400")

    def test_internal_gap_is_visible_despite_recent_snapshot(self):
        report = audit([snapshot(0), snapshot(3, 2)], contract())
        gap = report["workloads"][0]["gaps"]
        self.assertEqual(
            gap,
            [
                {
                    "start": "2026-01-01T01:00:00Z",
                    "end": "2026-01-01T03:00:00Z",
                    "duration_seconds": "7200",
                }
            ],
        )

    def test_leading_and_trailing(self):
        report = audit([snapshot(1)], contract())["workloads"][0]
        self.assertEqual(report["gap_count"], 2)
        self.assertEqual(report["uncovered_seconds"], "10800")

    def test_empty_expected_workload(self):
        report = audit([], contract())
        self.assertEqual(report["status"], "fail")
        self.assertIsNone(report["workloads"][0]["latest_before_until"])
        self.assertEqual(report["workloads"][0]["uncovered_seconds"], "14400")

    def test_before_window_anchor(self):
        point = snapshot(0, time="2025-12-31T23:30:00Z")
        report = audit([point], contract())["workloads"][0]
        self.assertEqual(report["covered_seconds"], "1800")
        self.assertEqual(report["gaps"][0]["start"], "2026-01-01T00:30:00Z")

    def test_too_old_anchor(self):
        point = snapshot(0, time="2025-12-31T23:00:00Z")
        report = audit([point], contract())["workloads"][0]
        self.assertEqual(report["covered_seconds"], "0")

    def test_future_never_fills_history(self):
        report = audit([snapshot(4), snapshot(5, 2)], contract())["workloads"][0]
        self.assertEqual(report["uncovered_seconds"], "14400")
        self.assertEqual(report["snapshots_at_or_after_until"], 2)

    def test_union_overlap_and_touching_boundaries(self):
        self.assertEqual(coverage([0, 1, 3, 5], 0, 7, 2), [])

    def test_points_are_clipped_to_window(self):
        self.assertEqual(coverage([-20, -1, 3, 10], 0, 6, 2), [(1, 3), (5, 6)])

    def test_randomized_interval_oracle(self):
        rng = random.Random(238119)
        for _ in range(400):
            points = [rng.randrange(-20, 80) for _ in range(rng.randrange(20))]
            age = rng.randrange(1, 15)
            gaps = coverage(points, 0, 60, age)
            actual = {t for a, b in gaps for t in range(a, b)}
            expected = {t for t in range(60) if not any(p <= t < p + age for p in points)}
            self.assertEqual(actual, expected)

    def test_nanoseconds_preserved(self):
        p = contract()
        p["since"] = "2026-01-01T00:00:00.000000001Z"
        p["until"] = "2026-01-01T00:00:00.000000009Z"
        self.assertEqual(audit([], p)["workloads"][0]["uncovered_seconds"], "0.000000008")

    def test_offsets_and_pre_epoch(self):
        self.assertEqual(
            timestamp("2026-01-01T08:00:00.123456789+08:00"),
            timestamp("2026-01-01T00:00:00.123456789Z"),
        )
        self.assertEqual(
            format_timestamp(timestamp("1960-01-01T00:00:00.000000001Z")),
            "1960-01-01T00:00:00.000000001Z",
        )

    def test_timestamp_rejections(self):
        for stamp in [
            None,
            "2026-02-30T00:00:00Z",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:60Z",
            "2026-01-01T00:00:00.1234567891Z",
            "2026-01-01T00:00:00+24:00",
            "2026-01-01T00:00:00-00:00",
        ]:
            with self.subTest(stamp=stamp), self.assertRaises(InputError):
                timestamp(stamp)

    def test_exact_path_set_and_hostname(self):
        data = [snapshot(0, paths=["/data", "/cache"]), snapshot(1, 2, hostname="other")]
        report = audit(data, contract())
        self.assertEqual(report["unmatched_snapshots"], 2)
        self.assertEqual(report["workloads"][0]["matching_snapshots"], 0)

    def test_path_order_ignored(self):
        p = contract()
        p["workloads"][0]["paths"] = ["/a", "/b"]
        report = audit([snapshot(0, paths=["/b", "/a"])], p)
        self.assertEqual(report["workloads"][0]["matching_snapshots"], 1)

    def test_tag_subset(self):
        p = contract()
        p["workloads"][0]["tags"] = ["daily"]
        report = audit(
            [snapshot(0, tags=["daily", "verified"]), snapshot(1, 2, tags=["hourly"])], p
        )
        self.assertEqual(report["workloads"][0]["matching_snapshots"], 1)

    def test_snapshot_null_tags_allowed(self):
        self.assertEqual(
            audit([snapshot(0, tags=None)], contract())["workloads"][0]["matching_snapshots"], 1
        )

    def test_false_tags_are_not_an_empty_array(self):
        for tags in [False, 0, "", {}]:
            with self.subTest(tags=tags), self.assertRaises(InputError):
                audit([snapshot(0, tags=tags)], contract())

    def test_permutation_determinism(self):
        data = [snapshot(0), snapshot(1, 2), snapshot(1, 3)]
        self.assertEqual(audit(data, contract()), audit(list(reversed(data)), contract()))

    def test_duplicate_id_rejected(self):
        with self.assertRaisesRegex(InputError, "Duplicate snapshot"):
            audit([snapshot(0), snapshot(1)], contract())

    def test_bad_snapshot_even_if_unmatched(self):
        with self.assertRaises(InputError):
            audit([snapshot(0, hostname="other", id="abc")], contract())

    def test_grouped_export_rejected(self):
        with self.assertRaises(InputError):
            audit([{"group_key": "server", "snapshots": []}], contract())

    def test_max_snapshots(self):
        with self.assertRaises(InputError):
            audit([snapshot(0), snapshot(1, 2)], contract(), max_snapshots=1)

    def test_gap_detail_cap_keeps_exact_totals(self):
        report = audit([snapshot(1)], contract(), max_gaps=0)["workloads"][0]
        self.assertEqual(report["gaps"], [])
        self.assertEqual(report["gap_count"], 2)
        self.assertEqual(report["omitted_gaps"], 2)
        self.assertEqual(report["uncovered_seconds"], "10800")

    def test_policy_typos_fail_closed(self):
        p = contract()
        p["workloads"][0]["max_ages_seconds"] = 20
        with self.assertRaises(InputError):
            audit([], p)

    def test_invalid_budget_and_version(self):
        for value in [True, 0, -1, 1.1, 315_360_001]:
            p = contract()
            p["workloads"][0]["max_age_seconds"] = value
            with self.subTest(value=value), self.assertRaises(InputError):
                audit([], p)
        p = contract()
        p["version"] = True
        with self.assertRaises(InputError):
            audit([], p)

    def test_no_vacuous_success(self):
        p = contract()
        p["workloads"] = []
        with self.assertRaises(InputError):
            audit([], p)

    def test_duplicate_workload_name(self):
        p = contract()
        p["workloads"].append(copy.deepcopy(p["workloads"][0]))
        with self.assertRaises(InputError):
            audit([], p)

    def test_multiple_expected_workloads(self):
        p = contract()
        p["workloads"].append({**p["workloads"][0], "name": "absent", "hostname": "missing"})
        result = audit([snapshot(h, h + 1) for h in range(4)], p)
        self.assertEqual([r["status"] for r in result["workloads"]], ["pass", "fail"])

    def test_nonpositive_window(self):
        p = contract()
        p["until"] = p["since"]
        with self.assertRaises(InputError):
            audit([], p)

    def test_duplicate_paths_and_invalid_unicode(self):
        for value in [["/a", "/a"], ["/bad\ud800"], ["/bad\n"]]:
            with self.subTest(paths=value), self.assertRaises(InputError):
                audit([snapshot(0, paths=value)], contract())


class JsonCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "snapshots.json"
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps(contract()), encoding="utf-8")

    def test_duplicate_key_and_nan_rejected(self):
        for value in ['{"x":1,"x":2}', "NaN", '{"a":Infinity}']:
            self.data.write_text(value)
            with self.subTest(value=value), self.assertRaises(InputError):
                read_json(self.data)

    def test_json_limit(self):
        self.data.write_text("[]")
        with self.assertRaises(InputError):
            read_json(self.data, 1)

    def test_utf8_bom(self):
        self.data.write_bytes(b"\xef\xbb\xbf[]")
        self.assertEqual(read_json(self.data), [])

    def test_cli_codes_and_inputs_unchanged(self):
        for value, expected in [([snapshot(h, h + 1) for h in range(4)], 0), ([], 1), ({}, 2)]:
            self.data.write_text(json.dumps(value))
            before = self.data.read_bytes(), self.policy.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                result = main([str(self.data), "--policy", str(self.policy), "--json"])
            self.assertEqual(result, expected)
            self.assertIn("status", json.loads(out.getvalue()))
            self.assertEqual(before, (self.data.read_bytes(), self.policy.read_bytes()))


if __name__ == "__main__":
    unittest.main()

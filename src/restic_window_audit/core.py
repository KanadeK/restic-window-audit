"""Offline interval coverage over explicitly retained recovery points."""

from __future__ import annotations

import json
import re
import stat
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

NANO = 1_000_000_000
EPOCH = datetime(1970, 1, 1)
STAMP = re.compile(
    r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z", re.ASCII
)
ID = re.compile(r"[0-9a-f]{64}\Z")


class InputError(Exception):
    pass


def timestamp(value: str) -> int:
    match = STAMP.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise InputError(
            "Timestamps must be RFC3339 with a timezone and at most 9 fractional digits"
        )
    date, clock, fraction, zone = match.groups()
    if zone == "-00:00":
        raise InputError("Unknown local offset -00:00 is unsupported; supply a known timezone")
    try:
        base = datetime.fromisoformat(date + "T" + clock)
        delta = base - EPOCH
        seconds = delta.days * 86400 + delta.seconds
        if zone != "Z":
            hour, minute = map(int, zone[1:].split(":"))
            if hour > 23 or minute > 59:
                raise ValueError("timezone offset out of range")
            seconds -= (1 if zone[0] == "+" else -1) * (hour * 3600 + minute * 60)
        ns = seconds * NANO + int((fraction or "0").ljust(9, "0"))
        # Confirm canonical UTC can also be represented within years 1..9999.
        format_timestamp(ns)
        return ns
    except (ValueError, OverflowError) as exc:
        raise InputError(f"Invalid timestamp: {value!r}") from exc


def format_timestamp(value: int) -> str:
    seconds, nanos = divmod(value, NANO)
    dt = EPOCH + timedelta(seconds=seconds)
    base = f"{dt.year:04}-{dt.month:02}-{dt.day:02}T{dt.hour:02}:{dt.minute:02}:{dt.second:02}"
    return base + (f".{nanos:09}".rstrip("0") if nanos else "") + "Z"


def decimal_seconds(value: int) -> str:
    whole, part = divmod(value, NANO)
    return str(whole) + (f".{part:09}".rstrip("0") if part else "")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _constant(value):
    raise InputError(f"Non-finite JSON value: {value}")


def read_json(path: Path, max_bytes: int = 64 * 1024**2):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise InputError("JSON byte limit must be positive")
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise InputError("JSON input must be a regular file")
        with path.open("rb") as source:
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise InputError("JSON input exceeds byte limit")
        return json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise InputError(f"Cannot read JSON: {exc}") from exc


def text(value, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise InputError(f"{where} must be a nonempty string of at most 4096 characters")
    if any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise InputError(f"{where} contains control characters or invalid Unicode")
    return value


def strings(value, where: str, *, nonempty: bool = False) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > 1000 or (nonempty and not value):
        raise InputError(
            f"{where} must be {'a nonempty' if nonempty else 'an'} array of at most 1000 strings"
        )
    result = [text(item, where) for item in value]
    if len(set(result)) != len(result):
        raise InputError(f"Duplicate value in {where}")
    return frozenset(result)


@dataclass(frozen=True)
class Snapshot:
    id: str
    time: int
    hostname: str
    paths: frozenset[str]
    tags: frozenset[str]


def snapshots(data, max_snapshots: int = 100_000) -> list[Snapshot]:
    if type(max_snapshots) is not int or max_snapshots <= 0:
        raise InputError("Snapshot limit must be positive")
    if not isinstance(data, list) or len(data) > max_snapshots:
        raise InputError("Expected ungrouped restic snapshots JSON array within --max-snapshots")
    result = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            raise InputError("Each snapshot must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise InputError("Snapshot id must be the full 64-character lowercase hexadecimal id")
        if identifier in seen:
            raise InputError(f"Duplicate snapshot id: {identifier}")
        seen.add(identifier)
        result.append(
            Snapshot(
                identifier,
                timestamp(item.get("time")),
                text(item.get("hostname"), "hostname"),
                strings(item.get("paths"), "paths", nonempty=True),
                strings([] if item.get("tags") is None else item["tags"], "tags"),
            )
        )
    return result


@dataclass(frozen=True)
class Workload:
    name: str
    hostname: str
    paths: frozenset[str]
    tags: frozenset[str]
    max_age: int


def policy(data) -> tuple[int, int, list[Workload]]:
    required = {"version", "since", "until", "workloads"}
    if (
        not isinstance(data, dict)
        or set(data) != required
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise InputError("Policy must contain exactly version=1, since, until and workloads")
    since, until = timestamp(data["since"]), timestamp(data["until"])
    if since >= until:
        raise InputError("Policy since must be earlier than until")
    items = data["workloads"]
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise InputError("Policy needs 1..1000 explicit workloads")
    result = []
    seen = set()
    for item in items:
        needed = {"name", "hostname", "paths", "max_age_seconds"}
        if not isinstance(item, dict) or not needed <= set(item) or set(item) - needed - {"tags"}:
            raise InputError(
                "Workload fields: name, hostname, paths, max_age_seconds, optional tags"
            )
        name = text(item["name"], "name")
        if name in seen:
            raise InputError(f"Duplicate workload name: {name!r}")
        seen.add(name)
        age = item["max_age_seconds"]
        if type(age) is not int or not 1 <= age <= 315_360_000:
            raise InputError("max_age_seconds must be an integer from 1 to 315360000")
        result.append(
            Workload(
                name,
                text(item["hostname"], "hostname"),
                strings(item["paths"], "paths", nonempty=True),
                strings(item.get("tags", []), "tags"),
                age * NANO,
            )
        )
    return since, until, result


def coverage(points: list[int], since: int, until: int, max_age: int) -> list[tuple[int, int]]:
    """Complement of union([s, s+max_age)) within [since, until)."""
    if since >= until or max_age <= 0:
        raise InputError("Invalid interval or age budget")
    cursor = since
    gaps = []
    for point in sorted(set(points)):
        start, end = max(point, since), min(point + max_age, until)
        if end <= since or start >= until:
            continue
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
        if cursor >= until:
            break
    if cursor < until:
        gaps.append((cursor, until))
    return gaps


def audit(data, contract, *, max_snapshots: int = 100_000, max_gaps: int = 200) -> dict:
    if type(max_gaps) is not int or max_gaps < 0:
        raise InputError("max_gaps must be a nonnegative integer")
    since, until, workloads = policy(contract)
    items = snapshots(data, max_snapshots)
    groups = defaultdict(list)
    for item in items:
        groups[(item.hostname, item.paths)].append(item)
    reports = []
    matched_ids = set()
    for workload in workloads:
        matches = sorted(
            (s for s in groups[(workload.hostname, workload.paths)] if workload.tags <= s.tags),
            key=lambda s: (s.time, s.id),
        )
        matched_ids.update(s.id for s in matches)
        gaps = coverage([s.time for s in matches], since, until, workload.max_age)
        missing = sum(end - start for start, end in gaps)
        usable = [s for s in matches if s.time < until]
        reports.append(
            {
                "name": workload.name,
                "status": "fail" if gaps else "pass",
                "hostname": workload.hostname,
                "paths": sorted(workload.paths),
                "required_tags": sorted(workload.tags),
                "max_age_seconds": workload.max_age // NANO,
                "matching_snapshots": len(matches),
                "snapshots_at_or_after_until": sum(s.time >= until for s in matches),
                "latest_before_until": {
                    "id": usable[-1].id,
                    "time": format_timestamp(usable[-1].time),
                }
                if usable
                else None,
                "covered_seconds": decimal_seconds(until - since - missing),
                "uncovered_seconds": decimal_seconds(missing),
                "gap_count": len(gaps),
                "omitted_gaps": max(0, len(gaps) - max_gaps),
                "gaps": [
                    {
                        "start": format_timestamp(start),
                        "end": format_timestamp(end),
                        "duration_seconds": decimal_seconds(end - start),
                    }
                    for start, end in gaps[:max_gaps]
                ],
            }
        )
    return {
        "schema_version": 1,
        "status": "fail" if any(r["status"] == "fail" for r in reports) else "pass",
        "basis": "retained_snapshot_timestamps",
        "window": {
            "since": format_timestamp(since),
            "until": format_timestamp(until),
            "duration_seconds": decimal_seconds(until - since),
        },
        "snapshot_count": len(items),
        "unmatched_snapshots": len(items) - len(matched_ids),
        "workloads": reports,
    }

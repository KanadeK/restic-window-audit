# restic-window-audit

Find the time intervals your **currently retained Restic recovery points** do not
cover, even when the most recent snapshot looks healthy.

Give this offline CLI a saved `restic snapshots --json` export and a policy listing
the workloads you expect. It computes leading, internal and trailing gaps for each
host/path/tag selection. A workload with no snapshots fails explicitly. It needs
no Restic installation, repository credentials, service or runtime dependencies.

This measures retained timestamp coverage. It does **not** prove historic backup
job uptime, successful completion, file inclusion, data integrity or application
recoverability. Retention can create gaps after backups originally succeeded.

## Install and run the demo

Python 3.11+:

```sh
git clone https://github.com/KanadeK/restic-window-audit.git
cd restic-window-audit
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
python examples/demo.py
```

The synthetic demo checks a two-hour historical gap hidden by a recent snapshot,
continuous coverage and a completely missing workload. It invokes the installed
CLI and removes temporary files. Release wheels and source distributions include
SHA256SUMS; install a verified wheel with `python -m pip install <wheel-file>`.
This project is not published to PyPI.

## Use a real snapshot export

Export without host/path/tag filters or `--group-by`, using your normal Restic
repository and credential setup. Only use the file if Restic exits successfully:

```sh
restic snapshots --json > snapshots.json
restic-window-audit snapshots.json --policy policy.json --json > report.json
```

The tool never runs Restic or accesses the backup repository. Use UTF-8 JSON
(an optional UTF-8 BOM is accepted); older PowerShell redirection may need
explicit UTF-8 encoding. Duplicate JSON keys, malformed snapshots and non-finite
numbers are errors, including malformed records that do not match your policy.

Create `policy.json` with your own dates and expected workloads:

```json
{
  "version": 1,
  "since": "2026-01-01T00:00:00Z",
  "until": "2026-01-01T04:00:00Z",
  "workloads": [
    {
      "name": "application",
      "hostname": "demo-server",
      "paths": ["/srv/app"],
      "tags": ["daily"],
      "max_age_seconds": 3600
    }
  ]
}
```

Matching is exact for `hostname` and the **set of root paths**, ignoring path order.
Paths are not normalized, expanded or interpreted as globs. A changed root-path
set is a different workload. Every required tag must be present; additional tags
are allowed. Omit `tags` to match any tags. This avoids a recent backup of `/etc`
silently satisfying a missing backup of `/srv/app`. Explicit expectations also
catch a host that has disappeared entirely from the export.

Policies reject unknown fields, duplicate workload names and duplicate paths/tags.
An empty workload list is an error, not a passing audit. Positive integer
`max_age_seconds` values up to 315360000 (ten 365-day years) are accepted.

## Exact time semantics

The audit window is **[since, until)**. A retained snapshot timestamp `s` contributes
the interval **[s, s + max_age_seconds)**. The report lists the complement of the
union of these intervals inside the window. For timestamps at 00:00 and 03:00,
a one-hour budget and window 00:00–04:00, the gap is 01:00–03:00 (7200 seconds).

Snapshots before `since` can cover the start of the window. Snapshots at or after
`until` cannot fill an earlier gap and are counted separately. Adjacent intervals
touch without a positive-duration gap. All arithmetic uses integer nanoseconds;
RFC3339 offsets and up to nine fractional digits are supported without floats.
Outputs use UTC. Duration values are decimal **strings** to preserve precision in
JSON consumers. Leap seconds, unknown offsets (`-00:00`) and timezone-free values
are rejected; use known timestamps in years 1–9999.

This model uses the snapshot's recorded source timestamp. It does not model the
time required to complete a backup or claim that a snapshot was available to an
operator at that instant. Restic timestamps are not authenticated by this tool.

## Reports, limits and exits

Each workload reports matching snapshot count, latest snapshot before `until`,
covered/uncovered seconds, gap count and interval details. Unmatched snapshots are
counted. Each workload defaults to at most 200 displayed gaps; `omitted_gaps` is
explicit and totals remain exact. `--max-gaps 0` retains totals only. Overlapping
workload reports must not be summed as independent global coverage.

Exit **0**: all workloads fully covered. **1**: at least one coverage gap. **2**:
input/policy/resource/CLI error. `--json` produces one object except argparse
usage errors. No input files are modified and no network calls are made.

Defaults: 64 MiB per input (`--max-json-bytes`), 100000 snapshots
(`--max-snapshots`), at most 1000 workloads and 1000 paths/tags per record. Inputs
are loaded in memory; this is not an unbounded streaming parser. Use stable
exports. Full lowercase 64-character snapshot IDs are required; grouped exports
and shortened IDs are unsupported. Extra Restic metadata fields are ignored.

Reports contain hostnames, root paths, tags and snapshot IDs. Keep them local or
review before sharing. This tool neither tests a restore nor calculates
Restic retention rules; supply the snapshots actually retained (or an explicitly
prepared hypothetical set) and interpret the result accordingly.

## Related tools and design evidence

[Restic scripting](https://restic.readthedocs.io/en/stable/075_scripting.html)
documents snapshot JSON. Its [retention documentation](https://restic.readthedocs.io/en/stable/060_forget.html)
explains how retained snapshots change over time. For continuous monitoring,
[restic-exporter](https://github.com/ngosang/restic-exporter) and Prometheus are
appropriate. This CLI provides a portable policy check and exact gap intervals
from an existing export, with no monitoring stack or repository password.

## Development and cleanup

```sh
python -m pip install -e . build ruff
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python -m build
```

Tests include 400 deterministic randomized interval cases checked against an
independent sampled-time oracle, nanosecond/timezone boundaries, workload matching,
invalid-input cases and CLI exits. CI runs Windows/Linux with Python 3.11/3.13.

Uninstall: `python -m pip uninstall restic-window-audit`; remove your virtual
environment and any exported JSON/report files you no longer need. The demo
cleans its own temporary files. No background service is installed. MIT license.

## 中文说明

读取 Restic 快照 JSON 和显式策略，检查当前保留恢复点在指定时间窗口中的覆盖空档。
按主机、完整根路径集合和所需标签匹配，因此其他任务的新快照不能掩盖漏备份的任务。
支持窗口之前的锚点、前后空档、纳秒精度和缺失主机。先运行 `python examples/demo.py`。
结果仅描述保留时间点的覆盖，不证明历史作业成功率、文件完整性或应用恢复成功；
保留策略删除旧快照也会产生空档。输入和报告可能包含私有路径，请谨慎分享。

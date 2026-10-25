# Security

Version 0.1.x receives fixes. Inputs may contain sensitive metadata. Reports can
expose names, timestamps, hashes or sizes; review before sharing. No telemetry,
network access or credential discovery is implemented.

For a vulnerability, use [GitHub private reporting](https://github.com/KanadeK/restic-window-audit/security/advisories/new).
If the private form is unavailable, open an issue containing only a request for
a private contact route; do not publish exploit details or sensitive data.
Provide a synthetic reproducer and the Python/tool version.

Resource limits are finite defaults, not a process sandbox. Use a current Python
release and OS isolation for untrusted large inputs. No Actions workflow writes
commits or updates dependencies automatically.

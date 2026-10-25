# Contributing

Open an issue with a small, synthetic reproducer and expected behavior. Never
attach private archives, backup inventories, credentials or production paths.

Use Python 3.11 or newer:

```sh
python -m venv .venv
# Activate it using your platform's normal command.
python -m pip install -e . build ruff
python -m unittest discover -s tests -v
python examples/demo.py
ruff check .
ruff format --check .
python -m build
```

Changes should include a regression test for observable behavior. Keep runtime
dependencies empty, errors explicit, reports deterministic and the input data
read-only. Discuss report-format changes before implementation. Contributions
are licensed under MIT; retain any required third-party attribution.

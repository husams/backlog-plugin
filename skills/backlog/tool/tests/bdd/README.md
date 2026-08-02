# E2E BDD tests

Run the scenarios from `skills/backlog/tool`:

```bash
uv run pytest tests/bdd
```

Run them with branch coverage and create `coverage-e2e.xml`:

```bash
uv run pytest tests/bdd \
  --cov=backlog_cli \
  --cov-config=pyproject.toml \
  --cov-report=term-missing \
  --cov-report=xml:coverage-e2e.xml
```

The coverage configuration measures the CLI subprocesses used by these E2E
scenarios and requires 99% total coverage.

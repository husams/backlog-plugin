# Project backlog configuration

This project uses the Backlog plugin's bundled `default-workflow.yaml`.
Because no project `workflow.yaml` is present, changes to the bundled default
are exercised directly by this repository.

`hooks.py` contains the project transition hooks. Both local agent operations
and GitHub Actions invoke them through the public action API.

The backlog data is stored in the configured backend. For the shared project
store, set:

```text
BACKLOG_DB=postgres
BACK_LOG_URL=<PostgreSQL connection URL>
BACKLOG_PROJECT=backlog-plugin
```

Never edit the database directly.

# Artifacts

Any file that belongs to a backlog item — a design note, spec, benchmark log,
review report, screenshot — is attached to it and stored under
`.backlog/artifacts/<KEY>/`.

```bash
$BL artifact add S-004 /tmp/cache-design.md --title "Cache design" --kind design
$BL artifact list S-004
```

`artifact add` **copies** the file into `.backlog/artifacts/<KEY>/<basename>` and
records it. Re-adding the same basename updates the title/kind and refreshes the
copy. Directories are copied recursively.

Suggested `--kind` values: `spec`, `design`, `plan`, `report`, `log`, `bench`,
`doc`. It is free text; stay consistent within a project.

## Rules

- Artifacts are **committed with the repository**, same as `backlog.db`. Do not
  attach anything you would not commit: no secrets, tokens, `.env` files, or
  credential-bearing command output.
- Write scratch work to `/tmp` first and attach only the finished artifact.
  `.backlog/artifacts/` is not a scratch directory.
- Reference artifacts from the item so a reader finds them: `$BL show S-004`
  lists them, and paths are stable at `.backlog/artifacts/<KEY>/<name>`.
- Long-lived knowledge that outlives the story — architecture, runbooks,
  research — belongs in the project wiki, not here. Attach the working document,
  link the durable one.

## Referencing an artifact in a review comment

```bash
$BL review open S-004 --author senior-developer \
  --body "Benchmark contradicts the design: see .backlog/artifacts/S-004/bench.log"
```

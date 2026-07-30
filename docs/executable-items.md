# Executable item contract

Task items remain plain text by default. Acceptance criteria and checklist
items become executable when shell or hook metadata is supplied through the
CLI or Python API; notes cannot declare execution. Existing stores are migrated
additively and existing criteria, checklist entries, and notes keep their
current behavior.

An executable item declares `requirement: required|advisory` (default:
`required`) and exactly one executor:

- `shell`: command, positive timeout, project-relative working directory,
  expected exit code, optional stdout/stderr matcher, and optional environment;
- `hook`: stable hook name, JSON-like arguments, positive timeout, and an
  expected JSON-like typed result.

`ExecutionSpec.fingerprint` is a canonical SHA-256 digest of only the execution
declaration. Changing task titles, descriptions, assignment, or workflow state
does not invalidate a result. Changing the executor, expectations, timeout, or
requirement does.

## Authoring and inspection

Existing plain syntax remains unchanged. Add `--shell COMMAND` or `--hook NAME`
to one acceptance criterion or checklist item to make it executable:

```bash
backlog story add --title "Validate package" \
  --ac "The unit suite passes" \
  --shell "python -m unittest" --stdout-contains "OK"

backlog item add S-001 --kind checklist --content "Policy accepts the release" \
  --hook checks.release --arguments '{"channel":"stable"}' \
  --expected-result '{"accepted":true}' --requirement advisory
```

Shell options include `--timeout`, `--working-directory`,
`--expected-exit-code`, one equals/contains/regex matcher per output stream,
and repeatable `--env NAME=VALUE`. Hook arguments and expected results are
JSON. `item set` and task `set --ac` accept the same execution options.
An executable operation accepts exactly one content line; omit the execution
options to retain the established multi-line plain-text behavior.

`item list` and `show` label each executable item as shell or hook, display
required/advisory and its current state, and show `pending` before its first
attempt. Environment variable names are shown, but values are always hidden in
both human-readable and JSON CLI output.

The Python API provides `create_feature`, `create_story`, `add_item`, and
`set_items`. Item inputs are either plain strings or mappings:

```python
with api.open(actor="planner") as backlog:
    story = backlog.create_story(
        "Validate package",
        acceptance_criteria=[{
            "content": "The unit suite passes",
            "execution": {
                "executor": "shell",
                "shell": {"command": "python -m unittest"},
            },
        }],
    )
    print(story.item_details())
```

`Task.items()` remains the backward-compatible text-only view.
`Task.item_details()` and `Task.executable_items()` return safe inspection
views; requested environment values are redacted to names.

## Trusted local policy

Execution policy is loaded only from
`.backlog/execution-policy.yaml` in the checkout where validation runs. It is
not stored in the shared backlog database. With no local file, shell execution
is disabled and no hooks are allowed.

```yaml
shell_enabled: true
allowed_working_directories: ["."]
allowed_environment_variables: ["CI"]
max_timeout_seconds: 120
max_output_bytes: 1048576
allowed_hooks: ["tests.unit", "lint.python"]
```

Runners must check `ExecutionPolicy.denial_reason()` before starting. A denial
is recorded as `skipped` with reason `policy_denied`; it is never `fail` or
`error`.

## Results, pending, and source identity

There is no result row before the first attempt; the single displayed state is
`pending`. Attempts have exactly four terminal statuses:

- `pass`: execution completed and every expectation matched;
- `fail`: execution completed but an expectation did not match;
- `error`: execution started but could not produce a comparable result;
- `skipped`: trusted local policy prevented execution from starting.

Only a result whose `spec_fingerprint` equals the current item fingerprint is
fresh. Every required item needs a fresh `pass` for
`required_validations_pass`; advisory outcomes and advisory pending items stay
visible but do not block acceptance.

Source identity is optional. A clean Git checkout records `HEAD`. A dirty
checkout also records a deterministic hash over tracked and non-ignored
untracked files. Ignored paths are excluded. A non-Git checkout remains
gate-eligible and persists `source_revision_unavailable`. `backlog doctor`
reports items whose latest fresh attempt still has that limitation; a later
source-identified attempt supersedes and clears the diagnostic.

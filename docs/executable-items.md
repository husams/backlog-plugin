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
and repeatable `--env NAME`. Environment values are resolved only from the
trusted local runtime. Hook arguments and expected results are
JSON. `item set` and task `set --ac` accept the same execution options.
An executable operation accepts exactly one content line; omit the execution
options to retain the established multi-line plain-text behavior.

`item list` and `show` label each executable item as shell or hook, display
required/advisory and its current state, and show `pending` before its first
attempt. Public views are value-opaque by default: commands, output matcher
values, hook arguments, and hook expected values are hidden. Environment
variable names are shown, but values are hidden. This applies equally to human
and JSON CLI output and to Python API inspection.

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
views; secret-bearing values are replaced with explicit hidden markers.

## Trusted local policy

Execution policy is loaded only from
`.backlog/execution.yaml` in the checkout where validation runs. It is
not stored in the shared backlog database. With no local file, shell execution
is disabled and no hooks are allowed.

```yaml
shell_enabled: true
allowed_commands: ["python3", "/usr/bin/make"]
allowed_working_directories: ["."]
allowed_environment_variables: ["CI"]
max_timeout_seconds: 120
max_output_bytes: 1048576
max_batch_seconds: 600
allowed_hooks: ["tests.unit", "lint.python"]
```

Runners must check `ExecutionPolicy.denial_reason()` before starting. A denial
is recorded as `skipped` with reason `policy_denied`; it is never `fail` or
`error`.

## Running shell validation

Run one declared shell item, or every shell item on a task:

```bash
backlog validation run 1457 --project-root .
backlog validation run-all S-008 --project-root .
backlog validation run-all S-008 --project-root . --fail-fast
```

The default batch behavior runs every item in declaration order. `--fail-fast`
stops after the first fail, error, or timeout. `max_batch_seconds` is a trusted
local wall-clock admission budget, separate from each item's timeout. If the
remaining batch budget is shorter than the next declared item timeout, that
item and every remaining item are audited as
`skipped/batch_budget_exhausted`; no process or check action is started.

The equivalent Python APIs are:

```python
with api.open(actor="validator") as bl:
    one = bl.run_item(1457, ".")
    all_results = bl.run_task("S-008", ".", fail_fast=False)
```

Commands are tokenized and spawned directly, never through a command shell.
When `allowed_commands` is non-empty, the stored command's first token must
match one of its entries. A shell item may request `output_limit_bytes`; policy
denies a request above `max_output_bytes`, otherwise the policy maximum applies.
The child receives only a minimal `PATH` plus explicitly declared,
policy-allowed environment values. The working directory is resolved inside
the explicit project root. Output capture is bounded across stdout and stderr;
requested environment values are redacted before results are returned or
audited.

Shell declarations store environment requests as names only:

```yaml
environment: ["CI", "VALIDATION_TOKEN"]
```

Values are resolved from the executing process environment only after trusted
local policy permits every requested name. A missing local value is a stable
pre-invocation error. Name-to-value mappings are rejected, so shared execution
specs, result rows, API results, and audit actions never receive the value.

Schema v10 combines shell result fields with the v9 named-hook result fields.
Opening an S-009-era v9 store additively installs the shell fields without
altering its hook results.

The returned `ExecutionResult` has `status`, `executor`, `expected`,
`actual_exit_code`, bounded `stdout` and `stderr`, `duration_ms`, `diagnostic`,
and `output_truncated`. Exit or matcher differences are `fail`. Startup and
runtime infrastructure problems are `error`. Timeout is `error/timed_out`.
Policy and batch admission denials are `skipped`.

An invocation emits `check.started` only after the process starts, followed by
exactly one terminal check action. Resolution and startup errors emit only
`check.failed`. Skipped attempts emit no check action and cannot alter task
status.

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

## Named validation hooks

Trusted project hooks live in the existing `.backlog/hooks` package. Its
`__init__.py` exports an exact-name mapping:

```python
from backlog_cli.api import ValidationHookResult, validation_hook

@validation_hook(version="billing-contract-v1")
def billing_contract(backlog, context, args):
    account = backlog.task(context.task_key)
    return ValidationHookResult(
        value={"valid": account.status != "incomplete"},
        detail=f"validated item {context.item_id}",
    )

validation_hooks = {"contracts.billing": billing_contract}
```

The same name must be listed in trusted local `.backlog/execution.yaml`:

```yaml
allowed_hooks: ["contracts.billing"]
max_timeout_seconds: 60
```

Run an item through `Backlog.run_hook_validation(item_id, actor=...,
project_root=...)`. The hook receives the typed backlog session, an immutable
`ValidationContext`, and the JSON-like arguments stored on the item. It must
return `ValidationHookResult`; its `value` is compared to `expected_result`
using typed JSON equality and normalized to the common `pass`/`fail`/`error`
contract.

Every run records the registered name and a canonical implementation identity.
Inspectable functions use `source_sha256:<digest>` after decorator unwrapping
and newline/trailing-whitespace normalization. `version:<value>` is used only
when source inspection is unavailable and the callable was registered with a
non-empty explicit version.

Policy denial produces `skipped/policy_denied` and no check action. Resolution
and identity failures produce a stable error and `check.failed` without
`check.started`. Once invocation begins, the runner emits `check.started`, then
`check.passed` or `check.failed`; a timeout emits `check.timed_out`. Timeouts
and exceptions are stable errors and never satisfy required validation gates.

Hook timeouts are enforced in-process with `SIGALRM`, so invocation is
supported only where `SIGALRM`, `ITIMER_REAL`, and `setitimer` are available
and the runner is on the process main thread. If either constraint is absent,
the callable is not invoked: the runner records
`error/hook_timeout_unavailable` with stable detail `sigalrm_unavailable` or
`main_thread_required`, and emits `check.failed` without `check.started`.

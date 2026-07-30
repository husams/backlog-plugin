# Executable item contract

Task items remain plain text by default. Acceptance criteria and checklist
items become executable only when an `executable_item` row is attached through
the Python API; notes cannot declare execution. Existing stores are migrated
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

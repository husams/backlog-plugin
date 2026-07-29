# Linear sync

Two-way, manual, and never destructive by default. Nothing talks to Linear
unless you run one of these commands.

```
Linear top-level issue (no parent)  <->  backlog feature   F-001
  Linear sub-issue                  <->  backlog story     S-001
    Linear sub-sub-issue            <->  backlog subtask   T-001
```

Anything deeper is flattened onto its nearest ancestor story and reported.

## Credentials

The key is read at run time and held only in memory. **Never pass it as an
argument.**

```bash
--vault-path secret/api-keys/linear   # preferred; --vault-field defaults to `key`
--token-file /path/to/key             # host without Vault
                                      # or export LINEAR_API_KEY
--from-json  dump.json                # a saved fetch; read-only, no credential
```

## The four commands

```bash
$BL linear status --vault-path secret/api-keys/linear --team HSE --project cidx
$BL linear pull   --vault-path secret/api-keys/linear --team HSE --project cidx
$BL linear push   --vault-path secret/api-keys/linear --team HSE --project cidx          # plan only
$BL linear push   --vault-path secret/api-keys/linear --team HSE --project cidx --apply  # writes
$BL linear sync   --vault-path secret/api-keys/linear --team HSE --project cidx --apply  # pull, then push
```

`status` sends nothing to Linear (it only adopts identity markers into the
local link table). **`push` writes nothing without `--apply`** — without it you
get the plan and can read it before anything leaves the machine.

`--project` filters to roots in that Linear project *plus every descendant*,
whatever project field the descendants carry (Linear sub-issues often carry
none).

## What is synced

| Field | pull | push |
| --- | --- | --- |
| title, description | yes | yes |
| acceptance criteria (`## Acceptance criteria` section) | yes | yes |
| priority (Urgent→P0, High→P1, Medium→P2, Low/None→P3) | yes | yes |
| status / workflow state | yes | yes |
| assignee | yes | only with `--push-assignee` |
| branch name, labels, PR reference in the body | yes | no |
| issue relations ↔ dependencies | yes | yes |

Status maps through state *type*, not state name. A push only moves the Linear
issue when the type actually differs, so a board that distinguishes "In
Progress" from "In Review" — both `started` — keeps its own finer-grained
choice instead of being flattened on every sync.

Pull walks the local status machine (Ready → In Progress → In Review →
Accepted → Done) rather than writing a status directly, so every gate still
applies. A Linear move that would need a backwards transition is reported under
`status_conflicts` and left alone.

## How it avoids clobbering anything

Each linked item records a fingerprint of both sides at the moment they last
agreed. Every run re-derives both and compares:

| Verdict | `pull` | `push` |
| --- | --- | --- |
| `unchanged` | nothing | nothing |
| `remote_ahead` | applies the Linear change | skips, tells you to pull |
| `local_ahead` | skips, tells you to push | sends the local change |
| `conflict` (both moved) | skips and reports | skips and reports |

Resolve a conflict by choosing a side explicitly:

```bash
$BL linear pull ... --prefer remote   # Linear wins for the conflicted items
$BL linear push ... --prefer local --apply
```

`linear status` shows the same verdicts without touching either side — run it
first when you are unsure.

## Identity

Identity lives in a link table, and a `<!-- linear:HSE-42 -->` marker is written
at the head of the local description so it survives an export/import or a
rebuilt store. The marker is stripped before any text is pushed back. Do not
edit it by hand.

```bash
$BL linear links                          # every bound item
$BL linear link  S-004 --issue HSE-42     # bind an item created locally
$BL linear unlink S-004
```

A store imported before the link table existed is adopted automatically on the
next pull, by marker.

## Creating and removing things

- Issues that disappear from a fetch (archived, deleted, moved out of scope) are
  **reported under `stale`, never deleted locally.**
- Local items with no Linear issue are ignored by push unless you pass
  `--create-missing`; their parent must already exist in Linear.
- Linear relations with no local counterpart are only removed with `--prune`.

## Dependencies

Linear relations map to backlog dependencies both ways:

| Linear | backlog |
| --- | --- |
| `blocks` | `blocks` |
| `related`, `similar` | `relates` |
| `duplicate` | `duplicates` |

So a Linear "blocks" relation becomes a real gate: the blocked story cannot move
to In Progress until its blocker is Accepted. See
[dependencies.md](dependencies.md). Pass `--no-relations` to skip that part of
the sync.

## Typical session

```bash
# what changed on either side?
$BL linear status --vault-path secret/api-keys/linear --team HSE --project cidx

# take Linear's changes
$BL linear pull --vault-path secret/api-keys/linear --team HSE --project cidx \
     --actor linear-sync

# ...work, move items, add dependencies...

# read the plan, then send it
$BL linear push --vault-path secret/api-keys/linear --team HSE --project cidx
$BL linear push --vault-path secret/api-keys/linear --team HSE --project cidx --apply
```

Save a fetch for offline replay or a dry run with `--save-fetch dump.json`, then
re-run anything read-only with `--from-json dump.json`.

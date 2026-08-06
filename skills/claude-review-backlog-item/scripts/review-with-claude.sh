#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  review-with-claude.sh [--check] --workdir PATH ITEM-KEY [REVIEW PROMPT]

Options:
  --workdir PATH  Git worktree where the item was implemented (required)
  --check         Validate the worktree and item without launching Claude
  -h, --help      Show this help
EOF
}

workdir=""
check_only=false

while (($#)); do
  case "$1" in
    --workdir)
      if (($# < 2)); then
        echo "error: --workdir requires a path" >&2
        exit 2
      fi
      workdir=$2
      shift 2
      ;;
    --check)
      check_only=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "$workdir" || $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

item_key=$1
review_prompt=${2:-}

if [[ ! "$item_key" =~ ^[[:alpha:]][[:alnum:]]*-[[:digit:]]+$ ]]; then
  echo "error: invalid Backlog item key: $item_key" >&2
  exit 2
fi

if [[ ! -d "$workdir" ]]; then
  echo "error: worktree directory does not exist: $workdir" >&2
  exit 2
fi

if ! repo_root=$(git -C "$workdir" rev-parse --show-toplevel 2>/dev/null); then
  echo "error: worktree is not inside a Git repository: $workdir" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
backlog_bin="$script_dir/../../backlog/bin/backlog"

if [[ ! -x "$backlog_bin" ]]; then
  echo "error: Backlog launcher is unavailable: $backlog_bin" >&2
  exit 2
fi

if ! (cd -- "$repo_root" && "$backlog_bin" show "$item_key" >/dev/null 2>&1); then
  echo "error: Backlog item $item_key is not visible from worktree: $repo_root" >&2
  exit 3
fi

if $check_only; then
  printf 'validated %s in %s\n' "$item_key" "$repo_root"
  exit 0
fi

claude_bin=${CLAUDE_BIN:-}
if [[ -z "$claude_bin" ]]; then
  claude_bin=$(command -v claude || true)
fi
if [[ -z "$claude_bin" || ! -x "$claude_bin" ]]; then
  echo "error: Claude executable is unavailable; install it or set CLAUDE_BIN" >&2
  exit 2
fi

if [[ -z "$review_prompt" ]]; then
  review_prompt="Use the backlog-reviewer skill to review $item_key as its assigned independent reviewer. Keep the implementation worktree read-only. Compare the implementation and tests with the Backlog description, acceptance criteria, and checklist. Run only non-mutating checks. Post every specific finding as a Backlog review thread with accurate severity and file/line evidence when available. Decide every pending implementer response. Record an evidenced acceptance verdict for every criterion, refuse an empty, stale, unmet, or unverified contract and any open todo, and finish with the configured acceptance or changes-requested action. Do not leave the review ambiguous."
elif [[ "$review_prompt" != *"$item_key"* ]]; then
  review_prompt="Review $item_key. $review_prompt"
fi

cd -- "$repo_root"
allowed_tools='Read,Grep,Glob,Bash(git status *),Bash(git diff *),Bash(git show *),Bash(git log *),Bash(git rev-parse *),Bash(backlog show *),Bash(backlog actions *),Bash(backlog gate *),Bash(backlog review *),Bash(backlog criteria *),Bash(backlog todo list *),Bash(backlog item list *),Bash(backlog history *),Bash(pytest *),Bash(uv run pytest *),Bash(make test*),Bash(npm test *),Bash(pnpm test *),Bash(yarn test *),Bash(cargo test *),Bash(go test *)'
exec "$claude_bin" \
  --permission-mode dontAsk \
  --tools "Read,Grep,Glob,Bash" \
  --allowedTools "$allowed_tools" \
  --no-chrome \
  --no-session-persistence \
  -n "$item_key" \
  -p "/backlog-reviewer $review_prompt"

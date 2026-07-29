# Sourced by ./backlog and ./backlog-py. Sets $VENV, provisioning the
# environment with uv the first time, after the lockfile changes, or when the
# needed extras change. Everything past this is a bare exec -- no uv, no
# subshell and no external binary in the hot path.

PROJECT="${BASH_SOURCE[0]%/*}/../tool"
VENV="$PROJECT/.venv"

# The PostgreSQL driver is an optional extra: install it only when BACKLOG_DB
# selects PostgreSQL or uses the legacy PostgreSQL-URL form.
_extras=()
case "${BACKLOG_DB:-}" in
  postgres|POSTGRES|Postgres|postgres://*|postgresql://*) _extras+=(--extra postgres) ;;
esac
for _e in ${BACKLOG_EXTRAS:-}; do _extras+=(--extra "$_e"); done

_stamp="$VENV/.backlog-stamp"
_want="${_extras[*]:-}"   # :- because bash 3.2 treats an empty array as unset under -u
_have=""
if [ -r "$_stamp" ]; then read -r _have < "$_stamp" || true; fi

if [ ! -x "$VENV/bin/backlog" ] || [ "$PROJECT/uv.lock" -nt "$_stamp" ] \
   || [ "$_have" != "$_want" ]; then
  UV="${UV_BIN:-}"
  if [ -z "$UV" ]; then
    if command -v uv >/dev/null 2>&1; then UV=uv
    elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"
    else
      echo "backlog: uv is required but was not found on PATH." >&2
      echo "  install it with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
      exit 127
    fi
  fi
  "$UV" sync --quiet --project "$PROJECT" ${_extras[@]+"${_extras[@]}"} >&2
  printf '%s\n' "$_want" > "$_stamp"
fi

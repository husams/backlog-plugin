"""CLI commands for acceptance criteria and their review verdicts."""

from __future__ import annotations

from .. import core
from .context import Ctx


def _lines(rows: list[dict]) -> str:
    if not rows:
        return "(no acceptance criteria)"
    out = []
    for row in rows:
        line = f"{row['position']:>3}  #{row['id']:<4} [{row['state']}] {row['content']}"
        if row["verdict_by"]:
            line += f"\n      verdict by {row['verdict_by']} at {row['verdict_at']}"
            if row["stale"]:
                line += " (stale: the criterion changed after it)"
            line += f"\n      evidence: {row['evidence']}"
        out.append(line)
    return "\n".join(out)


def cmd_criteria_list(ctx: Ctx, args) -> int:
    rows = core.list_criteria(ctx.conn, ctx.pid, args.key)
    ctx.emit(rows, _lines(rows))
    return 0


def cmd_criteria_verify(ctx: Ctx, args) -> int:
    row = core.record_verdict(
        ctx.conn,
        ctx.pid,
        args.id,
        met=not args.unmet,
        evidence=args.evidence,
        actor=args.actor,
    )
    ctx.emit(row, _lines([row]))
    return 0


def cmd_criteria_clear(ctx: Ctx, args) -> int:
    cleared = core.clear_verdicts(
        ctx.conn, ctx.pid, args.key, reason=args.reason, actor=args.actor
    )
    ctx.emit(
        {"key": core.normalize_key(args.key), "cleared": cleared},
        f"cleared {cleared} acceptance verdict(s)",
    )
    return 0

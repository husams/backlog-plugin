"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

from pathlib import Path

from .. import (
    core,
    review,
)
from ..render import (
    render_thread,
    row_to_dict,
    table,
)


from .context import Ctx


def cmd_pr_set(ctx: Ctx, args) -> int:
    row = core.set_pr(
        ctx.conn,
        ctx.pid,
        args.key,
        url=args.url,
        number=args.number,
        repo=args.repo,
        state=args.state,
        review_state=args.review_state,
        actor=args.actor,
    )
    ctx.emit(
        row_to_dict(row),
        f"{row['key']}  pr={row['pr_url'] or '#' + str(row['pr_number'])}  "
        f"state={row['pr_state']}  review={row['pr_review_state']}",
    )
    return 0


def cmd_pr_sync(ctx: Ctx, args) -> int:
    row = core.sync_pr(ctx.conn, ctx.pid, args.key, actor=args.actor)
    ctx.emit(
        row_to_dict(row),
        f"{row['key']}  pr={row['pr_url']}  state={row['pr_state']}  "
        f"review={row['pr_review_state']}",
    )
    return 0


def cmd_review_open(ctx: Ctx, args) -> int:
    t = review.open_thread(
        ctx.conn,
        ctx.pid,
        args.key,
        args.author,
        args.body,
        role=args.role,
        title=args.title or "",
        file_path=args.file,
        line=args.line,
        severity=args.severity,
    )
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_reply(ctx: Ctx, args) -> int:
    t = review.reply(
        ctx.conn,
        ctx.pid,
        args.comment,
        args.author,
        args.action,
        args.body,
        role=args.role,
    )
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_reopen(ctx: Ctx, args) -> int:
    t = review.reopen(
        ctx.conn, ctx.pid, args.root, args.author, args.body, role=args.role
    )
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_inbox(ctx: Ctx, args) -> int:
    threads = review.inbox(
        ctx.conn,
        ctx.pid,
        actor=args.actor,
        role=args.role,
        key=args.item,
        severity=args.severity,
    )
    text = (
        "\n\n".join(render_thread(t) for t in threads)
        if threads
        else "(no review threads waiting on you)"
    )
    ctx.emit(threads, text)
    return 0


def cmd_review_thread(ctx: Ctx, args) -> int:
    t = (
        review.full_thread(ctx.conn, args.root)
        if args.full
        else review.thread_summary(ctx.conn, args.root)
    )
    ctx.emit(t, render_thread(t, full=args.full))
    return 0


def cmd_review_audit(ctx: Ctx, args) -> int:
    result = review.audit(ctx.conn, ctx.pid, args.root)
    decisions = result["decisions"]
    text = (
        f"{result['root']}  reviewer={result['reviewer']}  state={result['state']}\n"
        + (
            "\n".join(
                f"{d['key']}  {d['at']}  {d['author']}  {d['action']}  {d['body']}"
                for d in decisions
            )
            if decisions
            else "(no accept/reject decisions)"
        )
    )
    ctx.emit(result, text)
    return 0


def cmd_review_list(ctx: Ctx, args) -> int:
    threads = review.list_threads(
        ctx.conn, ctx.pid, args.key, state=args.state, severity=args.severity
    )
    ctx.emit(
        threads,
        "\n\n".join(render_thread(t) for t in threads) if threads else "(no threads)",
    )
    return 0


def cmd_review_severity(ctx: Ctx, args) -> int:
    t = review.set_severity(
        ctx.conn, ctx.pid, args.root, args.severity, author=args.author
    )
    ctx.emit(t, render_thread(t))
    return 0


def cmd_artifact_add(ctx: Ctx, args) -> int:
    info = core.add_artifact(
        ctx.conn,
        ctx.dir,
        ctx.pid,
        args.key,
        Path(args.path),
        title=args.title or "",
        kind=args.kind,
        actor=args.actor,
    )
    ctx.emit(info, f"{info['key']}  <- .backlog/{info['rel_path']}")
    return 0


def cmd_artifact_list(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = core.list_artifacts(ctx.conn, task["id"])
    ctx.emit(
        [row_to_dict(r) for r in rows],
        table(
            ["PATH", "KIND", "TITLE"],
            [[f".backlog/{r['rel_path']}", r["kind"], r["title"]] for r in rows],
        ),
    )
    return 0

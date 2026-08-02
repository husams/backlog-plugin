"""Legacy v2 import and migration helpers."""

from __future__ import annotations

from .common import Conn, actor_kind, utcnow
from .resolution import StoreSpec

V2_TABLES = [
    "feature",
    "item",
    "dependency",
    "review_comment",
    "review_thread",
    "artifact",
    "event",
    "key_counter",
]
FEATURE_STATUS_IN = {
    "planned": "created",
    "active": "in_progress",
    "shipped": "done",
    "dropped": "incomplete",
}


def read_v2(conn: Conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in V2_TABLES:
        if not conn.table_exists(name):
            out[name] = []
            continue
        out[name] = [dict(r) for r in conn.execute(f"SELECT * FROM {name}").fetchall()]
    return out


def insert_project(
    conn: Conn,
    slug: str,
    spec: StoreSpec | None = None,
    name: str | None = None,
    description: str = "",
    template: str | None = None,
) -> int:
    from .. import templates

    templates.install_builtins(conn)
    tpl = templates.require(conn, template) if template else templates.default(conn)
    ts = utcnow()
    repo = str(spec.backlog_dir.parent) if spec and spec.backlog_dir else None
    return conn.insert_returning_id(
        "INSERT INTO project(template_id, slug, name, description, status, repo_path, "
        "created_at, updated_at) VALUES(?,?,?,?,'active',?,?,?)",
        (tpl["id"], slug, name or slug, description, repo, ts, ts),
    )


def load_v2_into_v3(
    conn: Conn, project_id: int, old: dict[str, list[dict]]
) -> list[str]:
    """Copy a v2 dataset into the v3 shape. Key -> new task id throughout."""
    notes: list[str] = []
    ids: dict[str, int] = {}

    def add_task(row: dict, task_type: str, parent_key: str | None) -> None:
        ts = row.get("created_at") or utcnow()
        status = row.get("status") or "created"
        if task_type == "feature":
            status = FEATURE_STATUS_IN.get(status, status)
        ids[row["key"]] = conn.insert_returning_id(
            "INSERT INTO task(project_id, key, task_type, parent_id, title, description, "
            "status, priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, "
            "branch, pr_url, pr_number, pr_repo, pr_state, pr_review_state, pr_waived, "
            "created_at, updated_at, closed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                row["key"],
                task_type,
                ids.get(parent_key) if parent_key else None,
                row.get("title") or row["key"],
                row.get("description") or "",
                status,
                row.get("priority") or "P2",
                row.get("owner"),
                row.get("assignee"),
                actor_kind(row.get("assignee")),
                row.get("reviewer"),
                actor_kind(row.get("reviewer")),
                row.get("branch"),
                row.get("pr_url"),
                row.get("pr_number"),
                row.get("pr_repo"),
                row.get("pr_state") or "none",
                row.get("pr_review_state") or "none",
                row.get("pr_waived") or 0,
                ts,
                row.get("updated_at") or ts,
                row.get("closed_at"),
            ),
        )

    for f in sorted(old["feature"], key=lambda r: r["key"]):
        add_task(f, "feature", None)
    items = {r["key"]: r for r in old["item"]}
    for r in sorted(old["item"], key=lambda r: r["key"]):
        if r.get("kind") == "story":
            add_task(r, "story", r.get("parent_key"))
    for r in sorted(old["item"], key=lambda r: r["key"]):
        if r.get("kind") == "subtask":
            add_task(r, "subtask", r.get("parent_key"))
    notes.append(f"{len(ids)} tasks migrated")

    # acceptance criteria text -> one task_item per line
    ac_rows = []
    ts = utcnow()
    for r in old["item"]:
        text = (r.get("acceptance_criteria") or "").strip()
        if not text or r["key"] not in ids:
            continue
        for pos, line in enumerate(l for l in text.splitlines() if l.strip()):
            ac_rows.append(
                (
                    ids[r["key"]],
                    "acceptance_criteria",
                    pos,
                    line.strip(),
                    0,
                    ts,
                    ts,
                    "migration",
                )
            )
    conn.executemany(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,?,?,?,?)",
        ac_rows,
    )
    if ac_rows:
        notes.append(
            f"{len(ac_rows)} acceptance-criteria lines split into task_item rows"
        )

    dep_rows = [
        (
            ids[d["from_key"]],
            ids[d["to_key"]],
            d["kind"],
            d.get("note") or "",
            d.get("external_id"),
            d.get("created_at") or ts,
            d.get("created_by"),
        )
        for d in old["dependency"]
        if d.get("from_key") in ids and d.get("to_key") in ids
    ]
    conn.executemany(
        "INSERT INTO dependency(from_task_id, to_task_id, kind, note, external_id, "
        "created_at, created_by) VALUES(?,?,?,?,?,?,?)",
        dep_rows,
    )
    dropped = len(old["dependency"]) - len(dep_rows)
    notes.append(
        f"{len(dep_rows)} dependencies migrated"
        + (f" ({dropped} dropped: endpoint missing)" if dropped else "")
    )

    conn.executemany(
        "INSERT INTO artifact(task_id, rel_path, title, kind, created_at, created_by) "
        "VALUES(?,?,?,?,?,?)",
        [
            (
                ids[a["entity_key"]],
                a["rel_path"],
                a.get("title") or "",
                a.get("kind") or "doc",
                a.get("created_at") or ts,
                a.get("created_by"),
            )
            for a in old["artifact"]
            if a.get("entity_key") in ids
        ],
    )
    conn.executemany(
        "INSERT INTO review_thread(task_id, root_key, state, resolution, title, file_path, "
        "line, last_comment_key, comment_count, opened_by, opened_at, updated_at, "
        "closed_by, closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                ids[t["target_key"]],
                t["root_key"],
                t["state"],
                t.get("resolution"),
                t.get("title") or "",
                t.get("file_path"),
                t.get("line"),
                t["last_comment_key"],
                t.get("comment_count") or 1,
                t["opened_by"],
                t["opened_at"],
                t["updated_at"],
                t.get("closed_by"),
                t.get("closed_at"),
            )
            for t in old["review_thread"]
            if t.get("target_key") in ids
        ],
    )
    conn.executemany(
        "INSERT INTO review_comment(task_id, key, root_key, parent_key, seq, author, "
        "author_kind, role, action, body, file_path, line, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                ids[c["target_key"]],
                c["key"],
                c["root_key"],
                c.get("parent_key"),
                c["seq"],
                c["author"],
                actor_kind(c["author"]),
                c["role"],
                c["action"],
                c["body"],
                c.get("file_path"),
                c.get("line"),
                c["created_at"],
            )
            for c in old["review_comment"]
            if c.get("target_key") in ids
        ],
    )
    conn.executemany(
        "INSERT INTO event(ts, project_id, task_id, entity_key, actor, actor_kind, kind, "
        "from_value, to_value, detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [
            (
                e["ts"],
                project_id,
                ids.get(e.get("entity_key")),
                e.get("entity_key") or "",
                e.get("actor"),
                actor_kind(e.get("actor")),
                e["kind"],
                e.get("from_value"),
                e.get("to_value"),
                e.get("detail") or "",
            )
            for e in old["event"]
        ],
    )
    conn.executemany(
        "INSERT INTO key_counter(project_id, prefix, next_value) VALUES(?,?,?)",
        [(project_id, k["prefix"], k["next_value"]) for k in old["key_counter"]],
    )
    conn.commit()
    notes.append(f"{len(old['event'])} history events carried over")
    return notes


def upgrade_required_validation_gates(conn: Conn) -> list[str]:
    """Make executable requirements part of every acceptance transition."""
    changed = 0
    for table in ("template_transition", "workflow_transition"):
        rows = conn.execute(
            f"SELECT id, gates FROM {table} WHERE to_status = 'accepted'"
        ).fetchall()
        for row in rows:
            gates = [g.strip() for g in (row["gates"] or "").split(",") if g.strip()]
            if "required_validations_pass" in gates:
                continue
            gates.append("required_validations_pass")
            conn.execute(
                f"UPDATE {table} SET gates = ? WHERE id = ?",
                (",".join(gates), row["id"]),
            )
            changed += 1
    conn.commit()
    return (
        [f"added required validation gate to {changed} acceptance transition(s)"]
        if changed
        else []
    )


def load_v2_export(
    conn: Conn, project_id: int, tables: dict[str, list[dict]]
) -> list[str]:
    """Public entry point for importing a v2 JSON dump into a v3 store."""
    old = {name: tables.get(name, []) for name in V2_TABLES}
    notes = load_v2_into_v3(conn, project_id, old)
    _resync_sequences(conn)
    return notes


# --------------------------------------------------------------------------- #


def _resync_sequences(conn: Conn) -> list[str]:
    from .projects import resync_sequences

    return resync_sequences(conn)

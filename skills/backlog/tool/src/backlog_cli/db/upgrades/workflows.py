"""Additive database upgrades grouped by responsibility."""

from __future__ import annotations

from ..common import Conn


def upgrade_bug_template_workflows(conn: Conn) -> list[str]:
    """Give every existing template a Bug workflow copied from its Story flow."""
    added = 0
    for template in conn.execute("SELECT id FROM template ORDER BY id").fetchall():
        exists = conn.execute(
            "SELECT id FROM template_workflow WHERE template_id = ? AND task_type = 'bug'",
            (template["id"],),
        ).fetchone()
        story = conn.execute(
            "SELECT * FROM template_workflow WHERE template_id = ? AND task_type = 'story'",
            (template["id"],),
        ).fetchone()
        if exists is not None or story is None:
            continue
        bug_id = conn.insert_returning_id(
            "INSERT INTO template_workflow(template_id, task_type, name, description) "
            "VALUES(?, 'bug', ?, ?)",
            (template["id"], "bug flow", story["description"]),
        )
        conn.execute(
            "INSERT INTO template_status(template_workflow_id, slug, display, category, "
            "position, satisfies_dependency, is_initial, is_terminal, description) "
            "SELECT ?, slug, display, category, position, satisfies_dependency, "
            "is_initial, is_terminal, description FROM template_status "
            "WHERE template_workflow_id = ?",
            (bug_id, story["id"]),
        )
        conn.execute(
            "INSERT INTO template_transition(template_workflow_id, from_status, to_status, "
            "gates, note) SELECT ?, from_status, to_status, gates, note "
            "FROM template_transition WHERE template_workflow_id = ?",
            (bug_id, story["id"]),
        )
        added += 1
    conn.commit()
    return [f"added Bug workflows to {added} template(s)"] if added else []


def upgrade_iteration_template_workflows(conn: Conn) -> list[str]:
    """Give existing templates the shipped three-state Iteration flow."""
    added = 0
    for template in conn.execute("SELECT id FROM template ORDER BY id").fetchall():
        exists = conn.execute(
            "SELECT id FROM template_workflow WHERE template_id=? AND task_type='iteration'",
            (template["id"],),
        ).fetchone()
        if exists is not None:
            continue
        wf_id = conn.insert_returning_id(
            "INSERT INTO template_workflow(template_id,task_type,name,description) "
            "VALUES(?,'iteration','iteration flow','Parallel unit of work')",
            (template["id"],),
        )
        conn.executemany(
            "INSERT INTO template_status(template_workflow_id,slug,display,category,position,"
            "satisfies_dependency,is_initial,is_terminal,description) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (wf_id, "planned", "Planned", "backlog", 0, 0, 1, 0, ""),
                (wf_id, "open", "Open", "active", 1, 0, 0, 0, ""),
                (wf_id, "closed", "Closed", "done", 2, 1, 0, 1, ""),
            ],
        )
        conn.executemany(
            "INSERT INTO template_transition(template_workflow_id,from_status,to_status,gates,note) "
            "VALUES(?,?,?,?,?)",
            [
                (wf_id, "planned", "open", "", "iteration.opened"),
                (
                    wf_id,
                    "open",
                    "closed",
                    "iteration_members_finished,iteration_comments_closed,"
                    "iteration_retrospective_actions_clear",
                    "iteration.closed",
                ),
                (
                    wf_id,
                    "closed",
                    "open",
                    "iteration_members_finished",
                    "iteration.reopened",
                ),
            ],
        )
        added += 1
    conn.commit()
    return [f"added Iteration workflows to {added} template(s)"] if added else []


def upgrade_iteration_feedback_flow(conn: Conn) -> list[str]:
    """Add all-severity comment closure policy to existing Iteration flows."""
    return _add_iteration_gate(
        conn,
        "iteration_comments_closed",
        "gated Iteration closure in {changed} transition(s)",
    )


def _add_iteration_gate(conn: Conn, gate: str, message: str) -> list[str]:
    changed = 0
    for workflow_table, transition_table, fk in (
        ("template_workflow", "template_transition", "template_workflow_id"),
        ("workflow", "workflow_transition", "workflow_id"),
    ):
        workflows = conn.execute(
            f"SELECT id FROM {workflow_table} WHERE task_type = 'iteration'"
        ).fetchall()
        for workflow_row in workflows:
            workflow_id = workflow_row["id"]
            close = conn.execute(
                f"SELECT id, gates FROM {transition_table} "
                f"WHERE {fk} = ? AND from_status = 'open' AND to_status = 'closed'",
                (workflow_id,),
            ).fetchone()
            if close is None:
                continue
            gates = [g.strip() for g in (close["gates"] or "").split(",") if g.strip()]
            if gate in gates:
                continue
            gates.append(gate)
            conn.execute(
                f"UPDATE {transition_table} SET gates = ? WHERE id = ?",
                (",".join(gates), close["id"]),
            )
            changed += 1
    conn.commit()
    return [message.format(changed=changed)] if changed else []


def upgrade_iteration_retrospective_action_gate(conn: Conn) -> list[str]:
    """Require every Created retrospective action to be triaged before closure."""
    return _add_iteration_gate(
        conn,
        "iteration_retrospective_actions_clear",
        "added retrospective action gate to {changed} Iteration transition(s)",
    )


def upgrade_feature_review_flow(conn: Conn) -> list[str]:
    """Add the missing scope-review path to shipped software-delivery flows."""
    transitions = [
        ("created", "in_review", ""),
        ("incomplete", "in_review", ""),
        ("in_review", "ready", "review_threads_closed"),
        ("in_review", "incomplete", ""),
    ]
    template_rows = conn.execute(
        "SELECT w.id FROM template_workflow w "
        "JOIN template t ON t.id = w.template_id "
        "WHERE t.slug = 'software-delivery' AND w.task_type = 'feature'"
    ).fetchall()
    project_rows = conn.execute(
        "SELECT w.id FROM workflow w "
        "JOIN project p ON p.id = w.project_id "
        "JOIN template t ON t.id = p.template_id "
        "WHERE t.slug = 'software-delivery' AND w.task_type = 'feature'"
    ).fetchall()
    for row in template_rows:
        conn.executemany(
            "INSERT INTO template_transition(template_workflow_id, from_status, "
            "to_status, gates) VALUES(?,?,?,?) "
            "ON CONFLICT(template_workflow_id, from_status, to_status) DO NOTHING",
            [
                (row["id"], source, target, gates)
                for source, target, gates in transitions
            ],
        )
    for row in project_rows:
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(workflow_id, from_status, to_status) DO NOTHING",
            [
                (row["id"], source, target, gates)
                for source, target, gates in transitions
            ],
        )
    conn.commit()
    count = len(template_rows) + len(project_rows)
    return [f"enabled feature scope review in {count} workflow(s)"] if count else []


def upgrade_todo_review_gates(conn: Conn) -> list[str]:
    """Gate every existing transition into a review-category state on todos."""
    changed = 0
    for status_table, transition_table, fk in (
        (
            "template_status",
            "template_transition",
            "template_workflow_id",
        ),
        ("workflow_status", "workflow_transition", "workflow_id"),
    ):
        transitions = conn.execute(
            f"SELECT tr.id,tr.gates FROM {transition_table} tr "
            f"JOIN {status_table} target ON target.{fk}=tr.{fk} "
            "AND target.slug=tr.to_status "
            f"JOIN {status_table} source ON source.{fk}=tr.{fk} "
            "AND source.slug=tr.from_status "
            "WHERE target.category='review' AND source.category!='review'"
        ).fetchall()
        for transition in transitions:
            gates = [
                gate.strip()
                for gate in (transition["gates"] or "").split(",")
                if gate.strip()
            ]
            if "todos_closed" in gates:
                continue
            gates.append("todos_closed")
            conn.execute(
                f"UPDATE {transition_table} SET gates=? WHERE id=?",
                (",".join(gates), transition["id"]),
            )
            changed += 1
    conn.commit()
    return [f"added todo review gate to {changed} transition(s)"] if changed else []


def upgrade_completion_gates(conn: Conn) -> list[str]:
    """Gate every existing transition into a finished state on todos and criteria.

    Reaching a done-category status is the moment work is claimed to be
    delivered, so both questions have to be answered there rather than only on
    the way into review, where the answers can still change afterwards. An
    Iteration is a container and carries neither, so its flows are left alone.
    """
    gates_wanted = ("todos_closed", "acceptance_criteria_verified")
    changed = 0
    for workflow_table, status_table, transition_table, fk in (
        (
            "template_workflow",
            "template_status",
            "template_transition",
            "template_workflow_id",
        ),
        ("workflow", "workflow_status", "workflow_transition", "workflow_id"),
    ):
        transitions = conn.execute(
            f"SELECT tr.id,tr.gates FROM {transition_table} tr "
            f"JOIN {workflow_table} w ON w.id=tr.{fk} "
            f"JOIN {status_table} target ON target.{fk}=tr.{fk} "
            "AND target.slug=tr.to_status "
            "WHERE target.category='done' AND w.task_type<>'iteration'"
        ).fetchall()
        for transition in transitions:
            gates = [
                gate.strip()
                for gate in (transition["gates"] or "").split(",")
                if gate.strip()
            ]
            missing = [gate for gate in gates_wanted if gate not in gates]
            if not missing:
                continue
            conn.execute(
                f"UPDATE {transition_table} SET gates=? WHERE id=?",
                (",".join([*gates, *missing]), transition["id"]),
            )
            changed += 1
    conn.commit()
    return [f"added completion gates to {changed} transition(s)"] if changed else []

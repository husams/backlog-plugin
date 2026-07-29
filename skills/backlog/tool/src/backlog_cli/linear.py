"""Two-way synchronisation between the backlog store and Linear.

Hierarchy mapping:

    Linear top-level issue (no parent)  <->  backlog feature
      Linear sub-issue                  <->  backlog story
        Linear sub-sub-issue            <->  backlog subtask

Anything deeper than a sub-sub-issue is flattened onto its nearest ancestor
story as a subtask, and every flattened item is reported.

Identity lives in the `linear_link` table (entity key <-> Linear issue), which
also carries the watermarks that let the sync tell a local edit from a remote
one instead of blindly overwriting. A `<!-- linear:HSE-42 -->` marker is still
written at the head of a synced description so identity survives an
export/import round trip or a rebuilt store; the marker is stripped before any
text is pushed back to Linear.

The credential is read at run time and held only in memory -- it is never
written to disk and never passed as a command-line argument.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import core, deps
from .db import BacklogError, Conn, Row, utcnow
from .schema import PRIORITIES

LINEAR_API = "https://api.linear.app/graphql"

MARKER_RE = re.compile(r"^<!-- linear:([A-Z][A-Z0-9]*-\d+) -->\s*$")
MARKER_ANYWHERE_RE = re.compile(r"<!-- linear:([A-Z][A-Z0-9]*-\d+) -->")


def marker(ident: str) -> str:
    return f"<!-- linear:{ident} -->"


# --------------------------------------------------------------------------- #
# field mappings
# --------------------------------------------------------------------------- #

# Linear priority: 0 None, 1 Urgent, 2 High, 3 Medium, 4 Low.
# "No priority" and "Low" both land on P3; that collision is deliberate -- the
# backlog has four levels and neither of those two is prioritised work.
PRIORITY_IN = {0: "P3", 1: "P0", 2: "P1", 3: "P2", 4: "P3"}
PRIORITY_OUT = {"P0": 1, "P1": 2, "P2": 3, "P3": 4}

# Linear state type -> the path the backlog status machine must walk to get
# there. There is only one route to each of these, so the walk is derived, not
# guessed.
WALK = {
    "backlog": [],
    "triage": [],
    "unstarted": ["ready"],
    "started": ["ready", "in_progress"],
    "completed": ["ready", "in_progress", "in_review", "accepted", "done"],
    "canceled": ["incomplete", "accepted"],
    "duplicate": ["incomplete", "accepted"],
}

# A feature has no review stage of its own, so its route to Done skips it.
FEATURE_WALK = {
    "backlog": [],
    "triage": [],
    "unstarted": ["ready"],
    "started": ["ready", "in_progress"],
    "completed": ["ready", "in_progress", "accepted", "done"],
    "canceled": ["incomplete", "accepted"],
    "duplicate": ["incomplete", "accepted"],
}


def walk_for(task_type: str) -> dict:
    return FEATURE_WALK if task_type == "feature" else WALK

# Backlog status -> acceptable Linear state types, best first. A push only
# moves the issue when its current state type is not already in this tuple, so
# a Linear board that distinguishes "In Progress" from "In Review" keeps its
# own finer-grained choice instead of being flattened on every sync.
STATE_TYPE_OUT = {
    "created": ("backlog", "triage", "unstarted"),
    "incomplete": ("backlog", "triage", "unstarted"),
    "ready": ("unstarted", "backlog"),
    "in_progress": ("started",),
    "in_review": ("started",),
    "needs_work": ("started",),
    "accepted": ("started",),
    "done": ("completed",),
}

# A feature never reaches In Review, so its outbound map is the same table
# minus that row; the shared one covers both.
FEATURE_STATE_TYPE_OUT = STATE_TYPE_OUT

RELATION_IN = {
    "blocks": "blocks",
    "duplicate": "duplicates",
    "related": "relates",
    "similar": "relates",
}
RELATION_OUT = {"blocks": "blocks", "duplicates": "duplicate", "relates": "related"}

CLOSED_REASON = {
    "canceled": "Canceled in Linear",
    "duplicate": "Marked duplicate in Linear",
}

PR_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

AC_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:[\d.]+\s*)?(acceptance\s+criteria|acceptance)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)


# --------------------------------------------------------------------------- #
# GraphQL
# --------------------------------------------------------------------------- #

QUERY_ISSUES = """
query Issues($after: String, $filter: IssueFilter) {
  issues(first: 100, after: $after, filter: $filter, includeArchived: false) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title description url branchName priority updatedAt
      state { name type }
      labels { nodes { name } }
      parent { identifier }
      project { id name }
      team { key }
      assignee { displayName }
      relations { nodes { id type relatedIssue { identifier } } }
    }
  }
}
"""

QUERY_TEAM = """
query Team($key: String!) {
  teams(filter: { key: { eq: $key } }, first: 1) {
    nodes {
      id key name
      states { nodes { id name type position } }
      projects { nodes { id name } }
    }
  }
}
"""

QUERY_USERS = """
query Users { users(first: 250) { nodes { id name displayName email active } } }
"""

MUT_ISSUE_UPDATE = """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success issue { id identifier url updatedAt state { name type } }
  }
}
"""

MUT_ISSUE_CREATE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success issue { id identifier url updatedAt state { name type } }
  }
}
"""

MUT_RELATION_CREATE = """
mutation RelationCreate($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) { success issueRelation { id type } }
}
"""

MUT_RELATION_DELETE = """
mutation RelationDelete($id: String!) { issueRelationDelete(id: $id) { success } }
"""


def read_token(vault_path: str | None, vault_field: str, token_file: str | None) -> str:
    """Resolve the Linear credential without it ever touching a command line.

    Vault first, so the key stays off the filesystem entirely; a file or the
    environment are fallbacks for hosts without Vault.
    """
    if vault_path:
        proc = subprocess.run(
            ["vault", "kv", "get", "-field", vault_field, vault_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise BacklogError(
                f"vault read of {vault_path} failed: {proc.stderr.strip()[:300]}"
            )
        token = proc.stdout.strip()
    elif token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        token = os.environ.get("LINEAR_API_KEY", "").strip()
    if not token:
        raise BacklogError(
            "no Linear credential: pass --vault-path (preferred), --token-file, "
            "or set LINEAR_API_KEY. Never pass the key itself as an argument."
        )
    return token


class Client:
    """Minimal Linear GraphQL client. Mutations are skipped unless `apply`."""

    def __init__(self, token: str, apply: bool = False, timeout: int = 60):
        self._headers = {
            "Content-Type": "application/json",
            # Personal API keys go in raw; OAuth access tokens need Bearer.
            "Authorization": token if token.startswith("lin_api_") else f"Bearer {token}",
        }
        self.apply = apply
        self.timeout = timeout
        self.reads = 0
        self.writes = 0
        self.skipped_writes = 0

    def call(self, query: str, variables: dict | None = None, mutation: bool = False):
        if mutation and not self.apply:
            self.skipped_writes += 1
            return None
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(LINEAR_API, data=payload, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise BacklogError(f"Linear API HTTP {exc.code}: {exc.read().decode()[:500]}")
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise BacklogError(f"Linear API unreachable: {exc.reason}")
        if body.get("errors"):
            raise BacklogError(f"Linear API errors: {json.dumps(body['errors'])[:800]}")
        if mutation:
            self.writes += 1
        else:
            self.reads += 1
        return body["data"]

    def fetch_issues(self, team: str | None) -> list[dict]:
        # NOTE: the project filter is applied locally, not here -- Linear
        # sub-issues do not always carry the parent's project, so filtering
        # server-side would silently drop stories and subtasks.
        filt: dict = {"team": {"key": {"eq": team}}} if team else {}
        nodes: list[dict] = []
        after = None
        while True:
            data = self.call(QUERY_ISSUES, {"after": after, "filter": filt or None})
            block = data["issues"]
            nodes.extend(block["nodes"])
            if not block["pageInfo"]["hasNextPage"]:
                return nodes
            after = block["pageInfo"]["endCursor"]

    def team(self, key: str) -> dict:
        nodes = self.call(QUERY_TEAM, {"key": key})["teams"]["nodes"]
        if not nodes:
            raise BacklogError(f"no Linear team with key {key!r}")
        return nodes[0]

    def users(self) -> list[dict]:
        return self.call(QUERY_USERS)["users"]["nodes"]


# --------------------------------------------------------------------------- #
# issue model
# --------------------------------------------------------------------------- #

@dataclass
class Issue:
    ident: str
    title: str
    issue_id: str = ""
    description: str = ""
    url: str = ""
    branch: str = ""
    priority: int = 0
    state_name: str = ""
    state_type: str = "backlog"
    updated_at: str = ""
    labels: list[str] = field(default_factory=list)
    parent: str | None = None
    project: str | None = None
    team: str = ""
    assignee: str | None = None
    relations: list[dict] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


def normalise(raw: dict) -> Issue:
    """Accept either the GraphQL node shape or the MCP list_issues shape."""
    ident = raw.get("identifier") or ""
    state = raw.get("state") or {}
    labels = raw.get("labels")
    if isinstance(labels, dict):  # GraphQL: {"nodes": [{"name": ...}]}
        labels = [n["name"] for n in labels.get("nodes", [])]
    labels = labels or []
    parent = raw.get("parent")
    if isinstance(parent, dict):
        parent = parent.get("identifier")
    parent = parent or raw.get("parentId")
    project = raw.get("project")
    if isinstance(project, dict):
        project = project.get("name")
    team = raw.get("team")
    if isinstance(team, dict):
        team = team.get("key") or ""
    assignee = raw.get("assignee")
    if isinstance(assignee, dict):
        assignee = assignee.get("displayName")
    priority = raw.get("priority")
    if isinstance(priority, dict):  # MCP shape: {"value": 2, "name": "High"}
        priority = priority.get("value", 0)
    rels = raw.get("relations")
    if isinstance(rels, dict):
        rels = rels.get("nodes", [])
    relations = [
        {
            "id": r.get("id") or "",
            "type": (r.get("type") or "").lower(),
            "other": (r.get("relatedIssue") or {}).get("identifier"),
        }
        for r in (rels or [])
        if (r.get("relatedIssue") or {}).get("identifier")
    ]
    return Issue(
        ident=ident,
        title=raw.get("title") or ident,
        issue_id=raw.get("id") or "",
        description=raw.get("description") or "",
        url=raw.get("url") or "",
        branch=raw.get("branchName") or raw.get("gitBranchName") or "",
        priority=int(priority or 0),
        state_name=state.get("name") or raw.get("status") or "",
        state_type=(state.get("type") or raw.get("statusType") or "backlog").lower(),
        updated_at=raw.get("updatedAt") or "",
        labels=labels,
        parent=parent if parent and parent != ident else None,
        project=project,
        team=team or "",
        assignee=assignee,
        relations=relations,
    )


def load_json(path: str) -> list[dict]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if "issues" in data:  # MCP list_issues dump
        return data["issues"]
    if "data" in data:  # raw GraphQL response
        return data["data"]["issues"]["nodes"]
    raise BacklogError(f"{path}: unrecognised dump shape")


def build_tree(issues: list[Issue], project: str | None) -> dict[str, Issue]:
    by_id = {i.ident: i for i in issues}
    for i in issues:
        if i.parent and i.parent not in by_id:
            i.parent = None  # parent filtered out or archived: promote to root
    for i in issues:
        if i.parent:
            by_id[i.parent].children.append(i.ident)

    if project:
        # Keep roots in the requested project plus every descendant, whatever
        # project field the descendants carry (Linear sub-issues often carry
        # none).
        keep: set[str] = set()
        stack = [i.ident for i in issues if not i.parent and i.project == project]
        while stack:
            ident = stack.pop()
            if ident in keep:
                continue
            keep.add(ident)
            stack.extend(by_id[ident].children)
        by_id = {k: v for k, v in by_id.items() if k in keep}
        for i in by_id.values():
            i.children = [c for c in i.children if c in by_id]
    return dict(sorted(by_id.items(), key=lambda kv: _num(kv[0])))


def _num(ident: str) -> int:
    try:
        return int(ident.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# text shaping
# --------------------------------------------------------------------------- #

def split_ac(description: str) -> tuple[str, str]:
    """Peel an 'Acceptance criteria' section off the description, if present."""
    m = AC_HEADING_RE.search(description)
    if not m:
        return description.strip(), ""
    body_start = m.end()
    nxt = NEXT_HEADING_RE.search(description, body_start)
    end = nxt.start() if nxt else len(description)
    ac = description[body_start:end].strip()
    rest = (description[: m.start()] + description[end:]).strip()
    return rest, ac


def describe(issue: Issue) -> str:
    """The local description: identity marker, a meta line, then the body."""
    meta = [f"**Linear {issue.ident}** — {issue.url}" if issue.url else f"**Linear {issue.ident}**"]
    if issue.labels:
        meta.append(f"Labels: {', '.join(issue.labels)}")
    meta.append(f"Linear state: {issue.state_name or issue.state_type}")
    body, _ = split_ac(issue.description)
    return "\n".join([marker(issue.ident), "", " · ".join(meta), "", body]).strip()


def local_body(description: str) -> str:
    """Inverse of `describe`: the prose without the marker and meta header."""
    lines = (description or "").splitlines()
    i = 0
    if i < len(lines) and MARKER_RE.match(lines[i].strip()):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].startswith("**Linear "):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    return "\n".join(lines[i:]).strip()


def remote_description(body: str, ac: str) -> str:
    body = (body or "").strip()
    ac = (ac or "").strip()
    if not ac:
        return body
    return (body + "\n\n## Acceptance criteria\n\n" + ac).strip()


def marker_ident(description: str) -> str | None:
    m = MARKER_ANYWHERE_RE.search(description or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# link table + change detection
# --------------------------------------------------------------------------- #

def _hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def local_payload(conn: Conn, task: Row) -> dict:
    """The locally-owned fields a sync cares about.

    Relations are excluded — they converge on their own and would otherwise
    mark both endpoints dirty whenever one edge moved.
    """
    ac = "\n".join(
        i["content"] for i in core.task_items(conn, task["id"], "acceptance_criteria")
    )
    return {
        "title": task["title"],
        "body": local_body(task["description"]),
        "ac": ac.strip(),
        "priority": task["priority"],
        "status": task["status"],
    }


def remote_payload(issue: Issue) -> dict:
    body, ac = split_ac(issue.description)
    return {
        "title": issue.title,
        "body": body.strip(),
        "ac": ac.strip(),
        "priority": PRIORITY_IN.get(issue.priority, "P3"),
        "status": issue.state_type,
    }


def get_link(conn: Conn, task_id: int) -> Row | None:
    return conn.execute("SELECT * FROM linear_link WHERE task_id = ?", (task_id,)).fetchone()


def link_by_ident(conn: Conn, ident: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM linear_link WHERE identifier = ?", (ident,)
    ).fetchone()


def all_links(conn: Conn, project_id: int) -> list[Row]:
    return conn.execute(
        "SELECT l.*, t.key FROM linear_link l JOIN task t ON t.id = l.task_id "
        "WHERE t.project_id = ? ORDER BY t.key",
        (project_id,),
    ).fetchall()


def upsert_link(conn: Conn, task_id: int, issue: "Issue | None" = None,
                identifier: str | None = None, **watermarks) -> None:
    ident = identifier or (issue.ident if issue else None)
    if not ident:
        raise BacklogError("upsert_link needs an issue or an identifier")
    fields: dict = {}
    if issue is not None:
        fields = {k: v for k, v in {
            "issue_id": issue.issue_id, "url": issue.url, "team_key": issue.team,
            "project_name": issue.project or "", "remote_updated_at": issue.updated_at,
        }.items() if v}
    fields.update({k: v for k, v in watermarks.items() if v is not None})
    conn.execute(
        "INSERT INTO linear_link(task_id, identifier) VALUES(?,?) "
        "ON CONFLICT(task_id) DO NOTHING",
        (task_id, ident),
    )
    sets, values = ["identifier = ?"], [ident]
    for name, value in fields.items():
        sets.append(f"{name} = ?")
        values.append(value)
    values.append(task_id)
    conn.execute(f"UPDATE linear_link SET {', '.join(sets)} WHERE task_id = ?", values)
    conn.commit()


def backfill_links(conn: Conn, project_id: int) -> list[str]:
    """Adopt tasks imported before the link table existed, via their marker."""
    added = []
    for row in conn.execute(
        "SELECT t.id, t.key, t.description FROM task t "
        "LEFT JOIN linear_link l ON l.task_id = t.id "
        "WHERE t.project_id = ? AND l.task_id IS NULL", (project_id,)
    ).fetchall():
        ident = marker_ident(row["description"])
        if not ident or link_by_ident(conn, ident) is not None:
            continue
        conn.execute("INSERT INTO linear_link(task_id, identifier) VALUES(?,?)",
                     (row["id"], ident))
        added.append(f"{row['key']} <- {ident}")
    if added:
        conn.commit()
    return added


def classify(conn: Conn, task: Row, issue: "Issue | None", link: Row | None) -> str:
    """unlinked | remote_missing | unsynced | unchanged | local_ahead |
    remote_ahead | conflict."""
    if link is None:
        return "unlinked"
    if issue is None:
        return "remote_missing"
    if not link["local_hash"] and not link["remote_hash"]:
        return "unsynced"
    local_changed = _hash(local_payload(conn, task)) != link["local_hash"]
    remote_changed = _hash(remote_payload(issue)) != link["remote_hash"]
    if local_changed and remote_changed:
        return "conflict"
    if local_changed:
        return "local_ahead"
    if remote_changed:
        return "remote_ahead"
    return "unchanged"


def stamp(conn: Conn, task_id: int, issue: Issue, direction: str) -> None:
    """Record both watermarks after a successful sync of one task."""
    task = core.get_task_by_id(conn, task_id)
    now = utcnow()
    upsert_link(
        conn, task_id, issue,
        local_hash=_hash(local_payload(conn, task)),
        remote_hash=_hash(remote_payload(issue)),
        **({"last_pull_at": now} if direction == "pull" else {"last_push_at": now}),
    )


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #

def _blank_report() -> dict:
    return {
        "created": [], "updated": [], "skipped": [], "conflicts": [],
        "status_conflicts": [], "unmapped_state": [], "flattened": [],
        "pr_linked": [], "stale": [], "relations": [], "notes": [],
    }


def pull(conn: Conn, project_id: int, issues_raw: list[dict], project: str | None,
         actor: str | None = None, prefer: str = "skip",
         sync_relations: bool = True) -> dict:
    """Bring local state in line with Linear, without clobbering local edits."""
    report = _blank_report()
    report["notes"] += [f"adopted {a}" for a in backfill_links(conn, project_id)]

    issues = [normalise(r) for r in issues_raw if r.get("identifier")]
    tree = build_tree(issues, project)
    if not tree:
        raise BacklogError("nothing in scope (check --team / --linear-project)")

    roots = [i for i in tree.values() if not i.parent]
    depth1 = sum(len(r.children) for r in roots)
    report["scope"] = {
        "source": len(issues), "in_scope": len(tree), "features": len(roots),
        "stories": depth1, "subtasks": len(tree) - len(roots) - depth1,
    }

    keys: dict[str, str] = {}
    for root in roots:
        keys[root.ident] = _pull_task(conn, project_id, root, "feature", None,
                                      report, actor, prefer)
        for child_id in sorted(root.children, key=_num):
            story = tree[child_id]
            story_key = _pull_task(conn, project_id, story, "story", keys[root.ident],
                                   report, actor, prefer)
            keys[child_id] = story_key
            stack = list(story.children)
            while stack:
                ident = stack.pop(0)
                node = tree[ident]
                if node.parent != child_id:
                    report["flattened"].append(f"{ident} (depth>3, attached to {child_id})")
                keys[ident] = _pull_task(conn, project_id, node, "subtask", story_key,
                                         report, actor, prefer)
                stack.extend(node.children)

    if sync_relations:
        _pull_relations(conn, project_id, tree, keys, report, actor)

    # Anything previously linked that this fetch no longer returns (archived,
    # deleted, or moved out of scope) is reported, never silently deleted.
    for link in all_links(conn, project_id):
        if link["identifier"] not in tree:
            report["stale"].append(f"{link['identifier']} -> {link['key']} not in this fetch")
    return report


def _resolve_task(conn: Conn, project_id: int, issue: Issue) -> Row | None:
    link = link_by_ident(conn, issue.ident)
    if link is None:
        return None
    row = conn.execute(
        "SELECT * FROM task WHERE id = ? AND project_id = ?",
        (link["task_id"], project_id),
    ).fetchone()
    if row is None:
        conn.execute("DELETE FROM linear_link WHERE identifier = ?", (issue.ident,))
        conn.commit()
    return row


def _pull_task(conn: Conn, project_id: int, issue: Issue, task_type: str,
               parent_key: str | None, report: dict, actor, prefer: str) -> str:
    task = _resolve_task(conn, project_id, issue)
    desc = describe(issue)
    _, ac = split_ac(issue.description)
    prio = PRIORITY_IN.get(issue.priority, "P3")

    if task is None:
        row = core.add_task(
            conn, project_id, task_type, issue.title, parent=parent_key,
            description=desc, priority=prio,
            assignee=issue.assignee if task_type != "feature" else None,
            branch=issue.branch or None, actor=actor,
        )
        if ac:
            core.set_items(conn, project_id, row["key"], "acceptance_criteria",
                           ac.splitlines(), actor=actor)
        _walk_status(conn, project_id, row["key"], issue, report, actor)
        stamp(conn, row["id"], issue, "pull")
        report["created"].append(f"{issue.ident} -> {row['key']} ({task_type}, {issue.state_name})")
        return row["key"]

    verdict = classify(conn, task, issue, get_link(conn, task["id"]))
    if verdict == "local_ahead" and prefer != "remote":
        report["skipped"].append(f"{issue.ident} ({task['key']}): local ahead — run `linear push`")
        return task["key"]
    if verdict == "conflict" and prefer != "remote":
        report["conflicts"].append(f"{issue.ident} ({task['key']}): both sides changed")
        return task["key"]
    if verdict == "unchanged":
        return task["key"]

    core.update_task(conn, project_id, task["key"], actor=actor, title=issue.title,
                     description=desc, priority=prio, branch=issue.branch or None)
    if ac:
        core.set_items(conn, project_id, task["key"], "acceptance_criteria",
                       ac.splitlines(), actor=actor)
    if issue.assignee and task["assignee"] != issue.assignee and task_type != "feature":
        core.assign(conn, project_id, task["key"], to=issue.assignee, actor=actor)
    _walk_status(conn, project_id, task["key"], issue, report, actor,
                 current=task["status"])
    stamp(conn, task["id"], issue, "pull")
    report["updated"].append(f"{issue.ident} -> {task['key']}")
    return task["key"]


def _record_pr(conn: Conn, project_id: int, key: str, issue: Issue,
               report: dict, actor) -> bool:
    """Attach the GitHub PR the Linear issue references, if it names one."""
    m = PR_RE.search(issue.description or "")
    if not m:
        return False
    owner, repo, number = m.groups()
    try:
        core.set_pr(conn, project_id, key, url=m.group(0), number=int(number),
                    repo=f"{owner}/{repo}", state="merged", review_state="approved",
                    actor=actor)
    except BacklogError:
        return False  # a feature carries no PR of its own
    report["pr_linked"].append(f"{issue.ident} -> {key} #{number}")
    return True


def _walk_status(conn: Conn, project_id: int, key: str, issue: Issue, report: dict,
                 actor, current: str = "created") -> None:
    task = core.get_task(conn, project_id, key)
    steps = walk_for(task["task_type"]).get(issue.state_type)
    if steps is None:
        report["unmapped_state"].append(f"{issue.ident}: {issue.state_type}")
        return
    if current != "created":
        if current in steps:
            steps = steps[steps.index(current) + 1:]
        else:
            report["status_conflicts"].append(
                f"{issue.ident} ({key}): local '{current}' cannot advance to Linear "
                f"'{issue.state_name}' — no legal transition, left as is"
            )
            return
    if not steps:
        return
    reason = CLOSED_REASON.get(issue.state_type)
    has_pr = (_record_pr(conn, project_id, key, issue, report, actor)
              if issue.state_type == "completed" else False)
    for step in steps:
        try:
            core.move(
                conn, project_id, key, step, actor=actor,
                reason=f"{reason} ({issue.state_name})" if reason else "",
                no_pr=not has_pr and step in ("in_review", "accepted", "done"),
                allow_open_children=step in ("accepted", "done"),
                allow_blocked=True,  # Linear is the authority on what has started
            )
        except BacklogError as exc:
            report["status_conflicts"].append(f"{issue.ident} ({key}) at '{step}': {exc}")
            return


def _pull_relations(conn: Conn, project_id: int, tree: dict[str, Issue],
                    keys: dict[str, str], report: dict, actor) -> None:
    # Read the whole edge set once. A typical board carries hundreds of
    # relations that are already recorded, and checking each one individually
    # would be hundreds of round trips against a remote database.
    present = {
        (r["from_key"], r["kind"], r["to_key"]): r["external_id"]
        for r in deps.all_edges(conn, project_id)
    }
    for issue in tree.values():
        src = keys.get(issue.ident)
        if not src:
            continue
        for rel in issue.relations:
            kind = RELATION_IN.get(rel["type"])
            if kind is None:
                report["relations"].append(f"{issue.ident}: unmapped relation {rel['type']!r}")
                continue
            dst = keys.get(rel["other"])
            if not dst:
                report["relations"].append(
                    f"{issue.ident} {rel['type']} {rel['other']}: other side out of scope"
                )
                continue
            pair = (src, kind, dst) if kind != "relates" else tuple(
                sorted((src, dst))[:1] + [kind] + sorted((src, dst))[1:]
            )
            if present.get((src, kind, dst), "") == rel["id"] or \
               present.get((dst, kind, src), "") == rel["id"]:
                continue
            try:
                res = deps.add(conn, project_id, src, dst, kind, actor=actor,
                               external_id=rel["id"])
                present[(res["from_key"], kind, res["to_key"])] = rel["id"]
                if res.get("created"):
                    report["relations"].append(f"+ {src} {kind} {dst} (from {issue.ident})")
            except BacklogError as exc:
                report["relations"].append(f"! {src} {kind} {dst}: {exc}")


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #

def _pick_state(states: list[dict], wanted_types: tuple[str, ...],
                current_type: str | None) -> dict | None:
    """Choose a Linear state, but leave the issue alone if it already fits."""
    if current_type in wanted_types:
        return None
    for want in wanted_types:
        candidates = [s for s in states if s["type"] == want]
        if candidates:
            return sorted(candidates, key=lambda s: (s.get("position") or 0, s["name"]))[0]
    return None


def plan_push(conn: Conn, project_id: int, remote: dict[str, Issue], team: dict | None,
              prefer: str = "skip", only: set[str] | None = None,
              create_missing: bool = False, push_assignee: bool = False,
              users: dict[str, str] | None = None) -> tuple[list[dict], dict]:
    """Work out, without writing anything, what a push would change."""
    report = _blank_report()
    plan: list[dict] = []
    states = (team or {}).get("states", {}).get("nodes", [])

    for task in conn.execute(
        "SELECT * FROM task WHERE project_id = ? ORDER BY key", (project_id,)
    ).fetchall():
        if only and task["key"] not in only:
            continue
        link = get_link(conn, task["id"])
        issue = remote.get(link["identifier"]) if link else None
        verdict = classify(conn, task, issue, link)

        if verdict == "unlinked":
            if create_missing:
                plan.append({"key": task["key"], "identifier": None, "action": "create",
                             "fields": ["title", "description", "priority", "state"],
                             "task": task})
            continue
        if verdict == "remote_missing":
            report["stale"].append(f"{task['key']} -> {link['identifier']} not in this fetch")
            continue
        if verdict == "remote_ahead" and prefer != "local":
            report["skipped"].append(
                f"{task['key']} ({link['identifier']}): remote ahead — run `linear pull`")
            continue
        if verdict == "conflict" and prefer != "local":
            report["conflicts"].append(
                f"{task['key']} ({link['identifier']}): both sides changed")
            continue
        if verdict == "unchanged":
            continue

        want = local_payload(conn, task)
        have = remote_payload(issue)
        fields, update = [], {}
        if want["title"] != have["title"]:
            fields.append("title")
            update["title"] = want["title"]
        desc = remote_description(want["body"], want["ac"])
        if desc != remote_description(have["body"], have["ac"]):
            fields.append("description")
            update["description"] = desc
        if want["priority"] != have["priority"]:
            fields.append("priority")
            update["priority"] = PRIORITY_OUT[want["priority"]]
        state = _pick_state(states, STATE_TYPE_OUT.get(want["status"], ()), issue.state_type)
        if state is not None:
            fields.append(f"state={state['name']}")
            update["stateId"] = state["id"]
        if push_assignee and task["task_type"] != "feature" and task["assignee"]:
            uid = (users or {}).get(task["assignee"].strip().lower())
            if uid:
                if task["assignee"] != issue.assignee:
                    fields.append("assignee")
                    update["assigneeId"] = uid
            else:
                report["notes"].append(
                    f"{task['key']}: assignee {task['assignee']!r} has no Linear user, not pushed")
        if not update:
            plan.append({"key": task["key"], "identifier": link["identifier"],
                         "action": "restamp", "fields": [], "issue": issue, "task": task})
            continue
        plan.append({"key": task["key"], "identifier": link["identifier"], "action": "update",
                     "fields": fields, "update": update, "issue": issue, "task": task})
    return plan, report


def apply_push(conn: Conn, project_id: int, client: Client, plan: list[dict],
               report: dict, team: dict | None, linear_project_id: str | None,
               actor: str | None = None) -> None:
    for step in plan:
        task = step["task"]
        if step["action"] == "restamp":
            stamp(conn, task["id"], step["issue"], "push")
            report["notes"].append(f"{step['key']}: re-stamped, nothing to send")
            continue
        if step["action"] == "create":
            issue = _create_issue(conn, project_id, client, step, team,
                                  linear_project_id, report)
            if issue is not None:
                stamp(conn, task["id"], issue, "push")
            continue
        data = client.call(MUT_ISSUE_UPDATE,
                           {"id": step["issue"].issue_id, "input": step["update"]},
                           mutation=True)
        if data is None:
            continue
        if not data["issueUpdate"]["success"]:
            report["notes"].append(f"{step['key']}: Linear rejected the update")
            continue
        fresh = _refresh(step["issue"], step["update"], data["issueUpdate"]["issue"])
        stamp(conn, task["id"], fresh, "push")
        report["updated"].append(
            f"{step['key']} -> {step['identifier']} ({', '.join(step['fields'])})")


def _refresh(issue: Issue, update: dict, node: dict) -> Issue:
    """Apply what we just sent back onto the local copy of the issue."""
    issue.title = update.get("title", issue.title)
    issue.description = update.get("description", issue.description)
    if "priority" in update:
        issue.priority = int(update["priority"])
    state = (node or {}).get("state") or {}
    if state:
        issue.state_name = state.get("name") or issue.state_name
        issue.state_type = (state.get("type") or issue.state_type).lower()
    issue.updated_at = (node or {}).get("updatedAt") or issue.updated_at
    return issue


def _create_issue(conn: Conn, project_id: int, client: Client, step: dict,
                  team: dict | None, linear_project_id: str | None,
                  report: dict) -> "Issue | None":
    if team is None:
        raise BacklogError("creating Linear issues needs --team")
    task = step["task"]
    want = local_payload(conn, task)
    parent_id = None
    if task["parent_id"]:
        parent_link = get_link(conn, task["parent_id"])
        if parent_link is None or not parent_link["issue_id"]:
            parent = core.get_task_by_id(conn, task["parent_id"])
            report["skipped"].append(
                f"{task['key']}: parent {parent['key']} is not in Linear yet")
            return None
        parent_id = parent_link["issue_id"]
    states = team.get("states", {}).get("nodes", [])
    state = _pick_state(states, STATE_TYPE_OUT.get(want["status"], ()), None)
    payload = {
        "teamId": team["id"],
        "title": want["title"],
        "description": remote_description(want["body"], want["ac"]),
        "priority": PRIORITY_OUT[want["priority"]],
    }
    if state:
        payload["stateId"] = state["id"]
    if parent_id:
        payload["parentId"] = parent_id
    elif linear_project_id:
        payload["projectId"] = linear_project_id
    data = client.call(MUT_ISSUE_CREATE, {"input": payload}, mutation=True)
    if data is None:
        report["created"].append(f"{task['key']} -> (new Linear issue)")
        return None
    node = data["issueCreate"]["issue"]
    issue = Issue(
        ident=node["identifier"], title=want["title"], issue_id=node["id"],
        description=payload["description"], url=node.get("url") or "",
        priority=payload["priority"],
        state_name=(node.get("state") or {}).get("name") or "",
        state_type=((node.get("state") or {}).get("type") or "backlog").lower(),
        updated_at=node.get("updatedAt") or "", team=team["key"],
    )
    # Stamp the marker into the local description so identity survives a rebuild.
    core.update_task(conn, project_id, task["key"], description=describe(issue))
    report["created"].append(f"{task['key']} -> {issue.ident}")
    return issue


def push_relations(conn: Conn, project_id: int, client: Client,
                   remote: dict[str, Issue], report: dict, prune: bool = False) -> None:
    """Create Linear relations for local edges that have no counterpart."""
    links = all_links(conn, project_id)
    ident_of = {l["key"]: l["identifier"] for l in links}
    id_of = {l["key"]: l["issue_id"] for l in links}
    seen_remote: set[tuple[str, str, str]] = set()
    for issue in remote.values():
        for rel in issue.relations:
            kind = RELATION_IN.get(rel["type"])
            if kind:
                seen_remote.add((issue.ident, kind, rel["other"]))

    for edge in deps.all_edges(conn, project_id):
        a, b = ident_of.get(edge["from_key"]), ident_of.get(edge["to_key"])
        if not a or not b:
            report["relations"].append(
                f"{edge['from_key']} {edge['kind']} {edge['to_key']}: "
                "an endpoint is not in Linear")
            continue
        if (a, edge["kind"], b) in seen_remote or (
            edge["kind"] == "relates" and (b, "relates", a) in seen_remote
        ):
            continue
        src_id, dst_id = id_of.get(edge["from_key"]), id_of.get(edge["to_key"])
        if not src_id or not dst_id:
            report["relations"].append(
                f"{a} {edge['kind']} {b}: missing Linear issue id, run `linear pull` first")
            continue
        data = client.call(
            MUT_RELATION_CREATE,
            {"input": {"issueId": src_id, "relatedIssueId": dst_id,
                       "type": RELATION_OUT[edge["kind"]]}},
            mutation=True,
        )
        report["relations"].append(f"+ {a} {edge['kind']} {b}")
        if data is not None and data["issueRelationCreate"]["success"]:
            conn.execute("UPDATE dependency SET external_id = ? WHERE id = ?",
                         (data["issueRelationCreate"]["issueRelation"]["id"], edge["id"]))
            conn.commit()

    if not prune:
        return
    local_edges = {(ident_of.get(e["from_key"]), e["kind"], ident_of.get(e["to_key"]))
                   for e in deps.all_edges(conn, project_id)}
    for issue in remote.values():
        for rel in issue.relations:
            kind = RELATION_IN.get(rel["type"])
            if not kind or (issue.ident, kind, rel["other"]) in local_edges:
                continue
            if kind == "relates" and (rel["other"], kind, issue.ident) in local_edges:
                continue
            client.call(MUT_RELATION_DELETE, {"id": rel["id"]}, mutation=True)
            report["relations"].append(f"- {issue.ident} {kind} {rel['other']} (pruned)")


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def status_report(conn: Conn, project_id: int, remote: dict[str, Issue]) -> dict:
    # Adopting markers into the link table is local bookkeeping, and without it
    # a store imported before the table existed would report as entirely
    # unlinked. Nothing is sent to Linear.
    backfill_links(conn, project_id)
    buckets: dict[str, list[str]] = {}
    for task in conn.execute(
        "SELECT * FROM task WHERE project_id = ? ORDER BY key", (project_id,)
    ).fetchall():
        link = get_link(conn, task["id"])
        issue = remote.get(link["identifier"]) if link else None
        verdict = classify(conn, task, issue, link)
        buckets.setdefault(verdict, []).append(
            f"{task['key']:<7} {link['identifier'] if link else '-':<9} {task['title'][:58]}")
    linked = {l["identifier"] for l in all_links(conn, project_id)}
    for ident in sorted(remote):
        if ident not in linked:
            buckets.setdefault("remote_only", []).append(
                f"{'-':<7} {ident:<9} {remote[ident].title[:58]}")
    return buckets

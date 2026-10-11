"""Facts about this turn for the system prompt's Environment block: where the agent works and what will ask first.

Everything here is read from the database (never from the model or a tool result) and is short, so it costs a few dozen
tokens. Nothing is started to find it out: a sandbox that does not exist yet is described as such, not created.
"""
from uuid import UUID

from sqlalchemy import select

from agentforge.database.models import Task
from agentforge.database.sessions import Conversation, TurnExecution
from agentforge.integrations.e2b_sandbox import WORKSPACE
from agentforge.tools.approvals import get_mode
from agentforge.tools.catalog import ASKS_BEFORE

WORKSPACE_TOOLS = frozenset({"bash", "read", "write", "edit", "grep", "find", "ls"})


def turn_environment(sessions, *, task_id: UUID, user_id: UUID, tools: tuple[str, ...]) -> dict[str, str]:
    """Labelled facts, in the order they are shown. Sync: call it in a thread."""
    with sessions() as db:
        grants = db.scalar(select(Task.permissions_json).where(Task.id == task_id)) or {}
        sandbox_id = db.scalar(select(Conversation.sandbox_id).join(TurnExecution, TurnExecution.session_id == Conversation.id)
                               .where(TurnExecution.task_id == task_id))
    facts: dict[str, str] = {}
    if WORKSPACE_TOOLS & set(tools):
        facts["Workspace"] = (f"{WORKSPACE}, a persistent Linux sandbox. " + (
            "It was started in an earlier turn, so files and changes from earlier turns should still be there."
            if sandbox_id else "Not started yet; it starts on the first workspace call."))
    if grants.get("attachments") and WORKSPACE_TOOLS & set(tools):
        from agentforge.integrations.attachment_storage import workspace_attachment_path
        facts["Attached files"] = "Available after the first workspace call: " + "; ".join(
            workspace_attachment_path(task_id, index, item["name"]) for index, item in enumerate(grants["attachments"]))
    repository = grants.get("repository") or {}
    if repository.get("repo_id"):
        ref = str(repository.get("ref") or "")
        facts["Repository"] = repository["repo_id"] + (f" at commit {ref[:12]}" if ref else "") + (
            ", checked out at the workspace root (base tag agentforge-base)" if WORKSPACE_TOOLS & set(tools) else "")
    plugins: dict[str, int] = {}
    for name in tools:
        if "__" in name:
            plugins[name.split("__", 1)[0]] = plugins.get(name.split("__", 1)[0], 0) + 1
    if plugins:
        facts["Plugins"] = ", ".join(f"{slug} ({count} tool{'s' if count != 1 else ''})" for slug, count in plugins.items())
    asking = sorted(ASKS_BEFORE & set(tools)) + (["plugin tools that change something"] if plugins else [])
    if asking:
        facts["Approvals"] = ("Ask me: " + ", ".join(asking) + " wait for the person's approval before they run."
                              if get_mode(sessions, user_id) == "ask" else "Approve for me: nothing waits for approval.")
    return facts

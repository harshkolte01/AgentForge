"""Facts about this turn for the system prompt's Environment block: where the agent works and what will ask first.

Everything here is read from the database (never from the model or a tool result) and is short, so it costs a few dozen
tokens. Nothing is started to find it out: a sandbox that does not exist yet is described as such, not created.
"""
import logging
from uuid import UUID

from sqlalchemy import select

from agentforge.database.delivery import ConversationDelivery
from agentforge.database.models import Task
from agentforge.database.sessions import Conversation, TurnExecution
from agentforge.integrations.e2b_sandbox import WORKSPACE
from agentforge.tools.approvals import get_mode
from agentforge.tools.catalog import ASKS_BEFORE

log = logging.getLogger(__name__)
WORKSPACE_TOOLS = frozenset({"bash", "read", "write", "edit", "grep", "find", "ls"})


def delivery_description(delivery: ConversationDelivery) -> str | None:
    """One short line on where this conversation's work stands on GitHub, or None if nothing has been published or is waiting."""
    pr, waiting = delivery.pr_json or {}, delivery.unpublished_json
    parts = []
    if delivery.branch and delivery.head_sha:
        parts.append(f"published to branch {delivery.branch}")
    if delivery.pr_number:
        checks = pr.get("checks") or {}
        detail = [pr.get("state") or "open"]
        if checks.get("state") in {"failing", "pending", "passing"}:
            detail.append(f"checks {checks['state']}" + (f" ({checks.get('failed')} failed)" if checks.get("failed") else ""))
        if pr.get("mergeable") == "conflicting":
            detail.append("merge conflicts")
        if (pr.get("reviews") or {}).get("decision") == "changes_requested":
            detail.append("changes requested")
        parts.append(f"pull request #{delivery.pr_number} ({', '.join(detail)}); get_pr_status shows the checks")
    if waiting:
        parts.append(f"{waiting.get('files', 0)} changed files are not pushed yet")
    if not parts:
        return None
    return ("; ".join(parts) + ". create_pull_request adds a commit to that branch and pull request (it never opens a second one). "
            "The person merges from the Changes panel; you cannot.")


def turn_environment(sessions, *, task_id: UUID, user_id: UUID, tools: tuple[str, ...]) -> dict[str, str]:
    """Labelled facts, in the order they are shown. Sync: call it in a thread."""
    with sessions() as db:
        grants = db.scalar(select(Task.permissions_json).where(Task.id == task_id)) or {}
        sandbox_id = db.scalar(select(Conversation.sandbox_id).join(TurnExecution, TurnExecution.session_id == Conversation.id)
                               .where(TurnExecution.task_id == task_id))
        try:
            delivery = db.scalar(select(ConversationDelivery).join(TurnExecution, TurnExecution.session_id == ConversationDelivery.conversation_id)
                                 .where(TurnExecution.task_id == task_id, ConversationDelivery.status == "active"))
            delivery_fact = delivery_description(delivery) if delivery is not None else None
        except Exception:
            # An extra line of context must never cost the turn its whole environment block (e.g. a database not yet migrated).
            db.rollback()
            log.warning("delivery facts unavailable: task_id=%s", task_id, exc_info=True)
            delivery_fact = None
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
    if delivery_fact and "create_pull_request" in tools:
        facts["GitHub delivery"] = delivery_fact
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

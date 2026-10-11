"""Owner-scoped, bounded inspection of the child behind one delegation call."""
from sqlalchemy import select

from agentforge.database.models import AgentRun, Project, Task, TaskEvent, ToolCall
from agentforge.tools.delegation import delegated_run_id
from agentforge.contracts.tasks import TERMINAL_STATUSES
from agentforge.repositories.activity import ToolCallNotFound


def read_delegation(sessions, task_id, call_id, owner_id):
    with sessions() as db:
        task = db.scalar(select(Task).join(Project).where(Task.id == task_id,
            Task.created_by == owner_id, Project.owner_id == owner_id))
        if task is None:
            raise ToolCallNotFound()
        call = db.scalar(select(ToolCall).where(ToolCall.id == call_id,
            ToolCall.task_id == task_id, ToolCall.tool_name == "delegate_task"))
        if call is None:
            raise ToolCallNotFound()
        run = db.scalar(select(AgentRun).where(AgentRun.id == delegated_run_id(call.operation_key),
            AgentRun.task_id == task_id, AgentRun.parent_run_id == call.agent_run_id))
        terminal = task.status in TERMINAL_STATUSES or call.finished_at is not None
        if run is None:
            return {"run_id": None, "title": "Subagent", "assignment": None,
                "status": "FAILED" if terminal else "STARTING", "summary": None,
                "error_code": call.error_code, "duration_ms": None, "events": [], "truncated": False}
        status = run.status
        if status == "RUNNING" and terminal:
            status = "CANCELLED" if task.status == "CANCELLED" else "INTERRUPTED"
        rows = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id,
            TaskEvent.payload["agent_run_id"].astext == str(run.id),
            TaskEvent.event_type.in_(("tool.started", "tool.finished", "assistant.message")))
            .order_by(TaskEvent.sequence.desc()).limit(201)))
        events = [{"task_id": str(task_id), "sequence": row.sequence, "event_type": row.event_type,
            "payload": {key: value for key, value in row.payload.items() if key != "parent_run_id"},
            "created_at": row.created_at.isoformat()} for row in reversed(rows[:200])]
        return {"run_id": str(run.id), "title": run.assignment_title or "Subagent",
            "assignment": run.assignment_prompt, "status": status,
            "summary": (run.result_json or {}).get("summary"), "error_code": run.error_code,
            "duration_ms": max(0, int((run.finished_at - run.started_at).total_seconds() * 1000)) if run.finished_at else None,
            "events": events, "truncated": len(rows) > 200}

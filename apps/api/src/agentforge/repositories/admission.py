"""One user row lock serializes public paid-work admission across API processes."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func, or_, text
from agentforge.database.models import User, Task
from agentforge.settings import get_settings

class AdmissionDenied(ValueError):
    pass

def admit_task(session, owner_id, max_cost_usd, settings=None):
    settings = settings or get_settings()
    session.execute(text("SELECT pg_advisory_xact_lock(578109563)"))
    active_states = ["CREATED", "PLANNING", "RUNNING", "VERIFYING", "RETRYING"]
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cost = Decimal(str(max_cost_usd))
    active_global = session.scalar(select(func.count()).select_from(Task).where(Task.status.in_(active_states)))
    count_global, reserved_global = session.execute(select(func.count(), func.coalesce(func.sum(Task.max_cost_usd), 0)).where(
        or_(Task.created_at >= midnight, Task.status.in_(active_states)))).one()
    if active_global >= settings.global_max_concurrent_tasks:
        raise AdmissionDenied("global_concurrent_task_limit")
    if count_global >= settings.global_max_daily_tasks:
        raise AdmissionDenied("global_daily_task_limit")
    if reserved_global + cost > settings.global_max_daily_cost_usd:
        raise AdmissionDenied("global_daily_cost_limit")
    user = session.scalar(select(User).where(User.id == owner_id).with_for_update())
    if user is None:
        raise AdmissionDenied("identity_unavailable")
    if user.github_user_id < 0:
        return
    if cost > settings.public_max_task_cost_usd:
        raise AdmissionDenied("task_cost_limit")
    active = session.scalar(select(func.count()).select_from(Task).where(Task.created_by == owner_id,
        Task.status.in_(active_states)))
    if active >= settings.public_max_concurrent_tasks:
        raise AdmissionDenied("concurrent_task_limit")
    count, reserved = session.execute(select(func.count(), func.coalesce(func.sum(Task.max_cost_usd), 0)).where(
        Task.created_by == owner_id, or_(Task.created_at >= midnight, Task.status.in_(active_states)))).one()
    if count >= settings.public_max_daily_tasks:
        raise AdmissionDenied("daily_task_limit")
    if reserved + cost > settings.public_max_daily_cost_usd:
        raise AdmissionDenied("daily_cost_limit")

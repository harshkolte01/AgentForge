"""Admission of a new task. There are no usage caps: the only check is that the owner exists."""
from sqlalchemy import select
from agentforge.database.models import User


class AdmissionDenied(ValueError):
    pass


def admit_task(session, owner_id, max_cost_usd=None, settings=None):
    if session.scalar(select(User.id).where(User.id == owner_id)) is None:
        raise AdmissionDenied("identity_unavailable")

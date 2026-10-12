"""A person's model preferences. Today that is the reasoning effort: kept on the server so it follows them to every
conversation and device, and read when a run starts so each run thinks as they chose."""
from uuid import UUID

from sqlalchemy import select, update

from agentforge.database.models import User
from agentforge.llm.effort import EFFORTS, default_for, efforts_for, resolve


class UnsupportedEffort(ValueError):
    """The model in use does not take the effort asked for."""


def stored_effort(sessions, user_id: UUID) -> str | None:
    with sessions() as db:
        return db.scalar(select(User.reasoning_effort).where(User.id == user_id))


def effort_for_run(sessions, user_id: UUID, model: str | None) -> str | None:
    """The effort a new run by this person uses, or None when the model offers no choice."""
    return resolve(model, stored_effort(sessions, user_id))


def effort_choices(sessions, user_id: UUID, model: str | None) -> dict:
    """What the picker shows: the efforts the model takes, the one in force and the default."""
    return {"model": model, "efforts": list(efforts_for(model)), "effort": effort_for_run(sessions, user_id, model),
            "default": default_for(model)}


def set_effort(sessions, user_id: UUID, model: str | None, effort: str) -> dict:
    if effort not in EFFORTS or effort not in efforts_for(model):
        raise UnsupportedEffort(effort)
    with sessions() as db, db.begin():
        db.execute(update(User).where(User.id == user_id).values(reasoning_effort=effort))
    return effort_choices(sessions, user_id, model)

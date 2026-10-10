"""Request-scoped repository factories; no provider calls in HTTP handlers."""

from fastapi import Request
from sqlalchemy.orm import sessionmaker

from agentforge.repositories.tasks import TaskRepository


def sessions(request: Request):
    return sessionmaker(bind=request.app.state.engine, expire_on_commit=False)


def task_repository(request: Request) -> TaskRepository:
    return TaskRepository(sessions(request))

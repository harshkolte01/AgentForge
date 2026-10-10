"""Immutable registry snapshots; transaction ownership stays with the registry."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agentforge.database.models import AgentDefinition, AgentDefinitionVersion, Project


class DefinitionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_version(self, *, definition_id: UUID, version: int) -> AgentDefinitionVersion | None:
        return self.session.scalar(select(AgentDefinitionVersion).where(
            AgentDefinitionVersion.definition_id == definition_id,
            AgentDefinitionVersion.version == version,
        ))

    def ensure_builtin(self, *, definition_id: UUID, agent_key: str, version: int,
                       definition_json: dict[str, Any], sha256: str) -> AgentDefinitionVersion:
        self.session.execute(insert(AgentDefinition).values(
            id=definition_id, namespace="builtin", project_id=None,
            agent_key=agent_key, current_version=version,
        ).on_conflict_do_nothing())
        definition = self.session.scalar(select(AgentDefinition).where(
            AgentDefinition.id == definition_id).with_for_update())
        if definition is None or definition.namespace != "builtin" or definition.agent_key != agent_key:
            raise ValueError("builtin_definition_conflict")
        if definition.disabled_at is not None:
            raise ValueError("definition_disabled")
        self.session.execute(insert(AgentDefinitionVersion).values(
            definition_id=definition_id, version=version,
            definition_json=definition_json, sha256=sha256,
        ).on_conflict_do_nothing(index_elements=["definition_id", "version"]))
        snapshot = self.get_version(definition_id=definition_id, version=version)
        if snapshot is None or snapshot.sha256 != sha256 or snapshot.definition_json != definition_json:
            raise ValueError("immutable_definition_conflict")
        definition.current_version = max(definition.current_version, version)
        return snapshot

    def resolve_project(self, *, project_id: UUID, agent_key: str,
                        version: int | None = None) -> AgentDefinitionVersion | None:
        definition = self.session.scalar(select(AgentDefinition).where(
            AgentDefinition.project_id == project_id, AgentDefinition.namespace == "project",
            AgentDefinition.agent_key == agent_key, AgentDefinition.disabled_at.is_(None),
        ))
        if definition is None:
            return None
        return self.get_version(definition_id=definition.id, version=version or definition.current_version)

    def save_project(self, *, project_id: UUID, agent_key: str, definition_json: dict[str, Any],
                     sha256: str, created_by: UUID, version: int) -> AgentDefinitionVersion:
        # Serializes creates too: locking a nonexistent definition would not prevent collisions.
        project = self.session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None or project.owner_id != created_by:
            raise ValueError("project_not_authorized")
        builtin = self.session.scalar(select(AgentDefinition.id).where(
            AgentDefinition.namespace == "builtin", AgentDefinition.agent_key == agent_key))
        if builtin is not None:
            raise ValueError("builtin_key_reserved")
        definition = self.session.scalar(select(AgentDefinition).where(
            AgentDefinition.project_id == project_id, AgentDefinition.agent_key == agent_key))
        if definition is None:
            if version != 1:
                raise ValueError("first_version_must_be_one")
            definition = AgentDefinition(project_id=project_id, namespace="project",
                                         agent_key=agent_key, current_version=1)
            self.session.add(definition)
            self.session.flush()
        elif definition.disabled_at is not None or version != definition.current_version + 1:
            raise ValueError("definition_version_conflict")
        elif definition.current_version >= 100:
            raise ValueError("agent_version_limit_reached")
        snapshot = AgentDefinitionVersion(definition_id=definition.id, version=version,
            definition_json=definition_json, sha256=sha256, created_by=created_by)
        self.session.add(snapshot)
        definition.current_version = version
        self.session.flush()
        return snapshot

    def disable_project(self, *, project_id: UUID, definition_id: UUID, owner_id: UUID) -> None:
        project = self.session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None or project.owner_id != owner_id:
            raise ValueError("project_not_authorized")
        definition = self.session.scalar(select(AgentDefinition).where(
            AgentDefinition.id == definition_id, AgentDefinition.project_id == project_id,
            AgentDefinition.namespace == "project").with_for_update())
        if definition is None:
            raise ValueError("definition_not_found")
        definition.disabled_at = datetime.now(timezone.utc)

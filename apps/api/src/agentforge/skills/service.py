"""A person's skills: the built-in ones (switch on or off) and their own (create, edit, delete), and what the agent is offered.

Every read and write is scoped to one person. A skill the agent can load is one that is switched on, so the list offered to the
agent and the skill it loads are always read from the same rules (`offered` and `load_offered`).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from agentforge.database.models import User
from agentforge.database.skills import SkillSetting, UserSkill

from .builtin import builtin_by_name, builtin_skills
from .format import SkillDocument, SkillInvalid, clean_description, clean_instructions, clean_name, validated

BUILTIN, CUSTOM = "builtin", "custom"


class SkillError(Exception):
    """A request that cannot be done: the HTTP status, a stable code, a sentence a person can act on and, for a bad field, which one."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


@dataclass(frozen=True)
class Offered:
    """One skill the agent may be told about and load."""
    name: str
    description: str
    source: str  # "builtin" or "custom"


def parse_key(key: str) -> tuple[str, str]:
    source, separator, ref = key.partition(":")
    if not separator or source not in (BUILTIN, CUSTOM) or not ref or len(ref) > 64:
        raise SkillError(404, "skill_not_found", "That skill does not exist.")
    return source, ref


def custom_view(row: UserSkill, *, detail: bool = False) -> dict[str, Any]:
    view = {"key": f"{CUSTOM}:{row.id}", "source": CUSTOM, "name": row.name, "description": row.description, "enabled": row.enabled,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}
    if detail:
        view["instructions"] = row.instructions
    return view


def builtin_view(skill: SkillDocument, enabled: bool, *, detail: bool = False) -> dict[str, Any]:
    view = {"key": f"{BUILTIN}:{skill.name}", "source": BUILTIN, "name": skill.name, "description": skill.description, "enabled": enabled,
            "updated_at": None}
    if detail:
        view["instructions"] = skill.instructions
    return view


def _invalid(error: SkillInvalid) -> SkillError:
    return SkillError(422, "skill_invalid", error.message, error.field)


class SkillService:
    def __init__(self, sessions, settings) -> None:
        self.sessions, self.settings = sessions, settings

    def _require_enabled(self) -> None:
        if not self.settings.skills_enabled:
            raise SkillError(403, "skills_disabled", "Skills are turned off on this server.")

    @staticmethod
    def _disabled_builtins(db, user_id: UUID) -> set[str]:
        return set(db.scalars(select(SkillSetting.skill_name).where(SkillSetting.user_id == user_id, SkillSetting.enabled.is_(False))))

    # --- reads -------------------------------------------------------------------------------------------------------
    def overview(self, user_id: UUID) -> dict[str, Any]:
        with self.sessions() as db:
            off = self._disabled_builtins(db, user_id)
            rows = list(db.scalars(select(UserSkill).where(UserSkill.user_id == user_id).order_by(UserSkill.created_at, UserSkill.id)))
            return {"enabled": self.settings.skills_enabled, "max_custom": self.settings.skill_max_per_user,
                    "builtin": [builtin_view(skill, skill.name not in off) for skill in builtin_skills()],
                    "custom": [custom_view(row) for row in rows]}

    def _row(self, db, user_id: UUID, ref: str, *, lock: bool = False) -> UserSkill:
        try:
            identifier = UUID(ref)
        except ValueError:
            raise SkillError(404, "skill_not_found", "That skill does not exist.") from None
        query = select(UserSkill).where(UserSkill.id == identifier, UserSkill.user_id == user_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise SkillError(404, "skill_not_found", "That skill does not exist.")
        return row

    def detail(self, user_id: UUID, key: str) -> dict[str, Any]:
        source, ref = parse_key(key)
        with self.sessions() as db:
            if source == CUSTOM:
                return custom_view(self._row(db, user_id, ref), detail=True)
            skill = builtin_by_name().get(ref)
            if skill is None:
                raise SkillError(404, "skill_not_found", "That skill does not exist.")
            return builtin_view(skill, ref not in self._disabled_builtins(db, user_id), detail=True)

    # --- changes -----------------------------------------------------------------------------------------------------
    def create(self, user_id: UUID, body) -> dict[str, Any]:
        self._require_enabled()
        try:
            skill = validated(body.name, body.description, body.instructions)
        except SkillInvalid as error:
            raise _invalid(error) from None
        if skill.name in builtin_by_name():
            raise SkillError(409, "skill_name_reserved", f"“{skill.name}” is the name of a built-in skill. Choose another name.", "name")
        try:
            with self.sessions() as db, db.begin():
                # Under the person's row lock, so two requests at once cannot pass the limit or take the same name.
                db.scalar(select(User).where(User.id == user_id).with_for_update())
                if db.scalar(select(func.count()).select_from(UserSkill).where(UserSkill.user_id == user_id)) >= self.settings.skill_max_per_user:
                    raise SkillError(409, "skill_limit_reached", f"You can have up to {self.settings.skill_max_per_user} skills. Delete one first.")
                if db.scalar(select(UserSkill.id).where(UserSkill.user_id == user_id, UserSkill.name == skill.name)) is not None:
                    raise SkillError(409, "skill_name_taken", f"You already have a skill named “{skill.name}”.", "name")
                row = UserSkill(user_id=user_id, name=skill.name, description=skill.description, instructions=skill.instructions,
                                enabled=bool(body.enabled))
                db.add(row)
                db.flush()
                db.refresh(row)
                return custom_view(row, detail=True)
        except IntegrityError:
            raise SkillError(409, "skill_name_taken", f"You already have a skill named “{skill.name}”.", "name") from None

    def update(self, user_id: UUID, key: str, patch) -> dict[str, Any]:
        self._require_enabled()
        source, ref = parse_key(key)
        fields = patch.model_fields_set
        if source == BUILTIN:
            if ref not in builtin_by_name():
                raise SkillError(404, "skill_not_found", "That skill does not exist.")
            if fields - {"enabled"} or patch.enabled is None:
                raise SkillError(422, "skill_builtin_readonly", "A built-in skill can be switched on or off, not edited. Copy it to change it.")
            with self.sessions() as db, db.begin():
                db.execute(insert(SkillSetting).values(user_id=user_id, skill_name=ref, enabled=patch.enabled).on_conflict_do_update(
                    index_elements=[SkillSetting.user_id, SkillSetting.skill_name],
                    set_={"enabled": patch.enabled, "updated_at": datetime.now(timezone.utc)}))
            return builtin_view(builtin_by_name()[ref], patch.enabled)
        try:
            cleaned = {"name": clean_name(patch.name) if "name" in fields else None,
                       "description": clean_description(patch.description) if "description" in fields else None,
                       "instructions": clean_instructions(patch.instructions) if "instructions" in fields else None}
        except SkillInvalid as error:
            raise _invalid(error) from None
        if cleaned["name"] in builtin_by_name():
            raise SkillError(409, "skill_name_reserved", f"“{cleaned['name']}” is the name of a built-in skill. Choose another name.", "name")
        try:
            with self.sessions() as db, db.begin():
                row = self._row(db, user_id, ref, lock=True)
                if cleaned["name"] is not None and cleaned["name"] != row.name:
                    if db.scalar(select(UserSkill.id).where(UserSkill.user_id == user_id, UserSkill.name == cleaned["name"])) is not None:
                        raise SkillError(409, "skill_name_taken", f"You already have a skill named “{cleaned['name']}”.", "name")
                    row.name = cleaned["name"]
                if cleaned["description"] is not None:
                    row.description = cleaned["description"]
                if cleaned["instructions"] is not None:
                    row.instructions = cleaned["instructions"]
                if "enabled" in fields and patch.enabled is not None:
                    row.enabled = patch.enabled
                row.updated_at = datetime.now(timezone.utc)
                db.flush()
                return custom_view(row, detail=cleaned["instructions"] is not None)
        except IntegrityError:
            raise SkillError(409, "skill_name_taken", "You already have a skill with that name.", "name") from None

    def delete(self, user_id: UUID, key: str) -> None:
        source, ref = parse_key(key)
        if source == BUILTIN:
            raise SkillError(422, "skill_builtin_readonly", "A built-in skill cannot be deleted. Switch it off instead.")
        with self.sessions() as db, db.begin():
            row = self._row(db, user_id, ref, lock=True)
            db.execute(delete(UserSkill).where(UserSkill.id == row.id))


# --- what the agent is offered ---------------------------------------------------------------------------------------
def offered(sessions, user_id: UUID) -> list[Offered]:
    """The skills switched on for this person, built-in first (in their own order), then theirs oldest first. Names never repeat."""
    with sessions() as db:
        off = SkillService._disabled_builtins(db, user_id)
        result = [Offered(skill.name, skill.description, BUILTIN) for skill in builtin_skills() if skill.name not in off]
        rows = db.execute(select(UserSkill.name, UserSkill.description).where(UserSkill.user_id == user_id, UserSkill.enabled.is_(True))
                          .order_by(UserSkill.created_at, UserSkill.id)).all()
    taken = {item.name for item in result}
    result += [Offered(name, description, CUSTOM) for name, description in rows if name not in taken]
    return result


def load_offered(sessions, user_id: UUID, name: str) -> tuple[SkillDocument, str] | None:
    """The full text of the skill `name` if it is switched on for this person, and where it came from; else None."""
    if name in builtin_by_name():
        with sessions() as db:
            if name in SkillService._disabled_builtins(db, user_id):
                return None
        return builtin_by_name()[name], BUILTIN
    with sessions() as db:
        row = db.scalar(select(UserSkill).where(UserSkill.user_id == user_id, UserSkill.name == name, UserSkill.enabled.is_(True)))
        return (SkillDocument(row.name, row.description, row.instructions), CUSTOM) if row is not None else None

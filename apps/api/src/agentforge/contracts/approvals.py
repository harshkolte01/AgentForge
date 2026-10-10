"""Exact action decisions never accept a replacement action from the browser."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

def canonical_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

class ApprovalPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=10000)

    @field_validator("title", "body")
    @classmethod
    def safe_text(cls, value):
        if "\x00" in value or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("unsupported control character")
        return value

class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "deny"]

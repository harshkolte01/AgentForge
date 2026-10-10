"""Keep graph checkpoints small: durable content lives in application tables."""
from typing import TypedDict


class ParentState(TypedDict, total=False):
    task_id: str
    active_step_id: str | None
    plan_ready: bool
    exhausted: bool
    verification_status: str | None
    final_status: str | None
    coding_attempt: int
    coding_executor_status: str
    coding_test_success: bool
    coding_test_ids: list[str]
    coding_test_artifact_ids: list[str]
    coding_revision: str
    coding_unknown: bool
    coding_review_success: bool
    coding_review_ids: list[str]
    coding_diff_id: str
    coding_diff_artifact_id: str
    coding_secret_check: bool

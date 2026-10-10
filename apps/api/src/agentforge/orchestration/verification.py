"""Conservative citation verification over durable specialized child results.

Task ownership only establishes authorization, never factual support. PASS in
this slice certifies verbatim quote coverage of fetched snapshots. Paraphrases,
semantic inference, contradiction resolution and coverage of a broad user goal
remain explicit limitations rather than invented semantic verification.
"""

from collections.abc import Awaitable, Callable
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from agentforge.contracts.subagent import ResearchClaim, ResearchResult, SubagentResult
from agentforge.integrations.artifact_storage import ArtifactStorage
from agentforge.repositories.research import ResearchRepository
from agentforge.security.policies import AuthorizationService
from agentforge.settings import get_settings
from agentforge.subagents.scope import ScopeDenied, TaskContext


class ClaimCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    claim: str
    source_ids: tuple[str, ...] = ()
    status: Literal["QUOTE_SUPPORTED", "UNSUPPORTED", "UNREACHABLE", "MISSING", "UNRESOLVED"]
    reason: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["PASS", "PARTIAL", "FAIL"]
    summary: str
    checks: tuple[ClaimCheck, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _markdown_text(text: str) -> str:
    # External/model content is always text, never raw HTML, executable URLs,
    # images or Markdown links. Source hyperlinks are generated from checked
    # HTTPS records below. UI source rendering must remain plain text as well.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()!#|])", r"\\\1", text)


class VerificationService:
    def __init__(self, session_factory: Callable[[], Session], *, storage: ArtifactStorage | None = None) -> None:
        self.repository = ResearchRepository(session_factory)
        self.authorizer = AuthorizationService(session_factory)
        self.storage = storage or ArtifactStorage(get_settings().research_storage_path)

    async def verify_result(self, ctx: TaskContext, result: SubagentResult, *,
                            acceptance_criteria: tuple[str, ...] = (),
                            authority: Callable[[], Awaitable[None]] | None = None) -> VerificationResult:
        if authority is not None:
            await authority()
        await self.authorizer.check_context(ctx)
        # Fetch the specialized JSON kept by SpawnService. Its common handoff
        # intentionally drops claims and cannot substitute summary text for them.
        payloads = self.repository.research_results(ctx)
        claims: list[ResearchClaim] = []
        unknowns: list[str] = []
        incomplete = result.status != "PASS"
        for payload in payloads:
            try:
                specialized = ResearchResult.model_validate_json(json.dumps(payload))
            except (ValidationError, ValueError):
                incomplete = True
                continue
            if specialized.status != "PASS":
                incomplete = True
            claims.extend(specialized.claims)
            unknowns.extend(specialized.unknowns)
        if isinstance(result, ResearchResult) and not payloads:
            claims.extend(result.claims)
            unknowns.extend(result.unknowns)
        # Deduplicate without losing actual claim text from the durable run.
        distinct: dict[tuple[str, tuple[str, ...]], ResearchClaim] = {}
        for claim in claims:
            distinct[(claim.claim, claim.source_ids)] = claim
        checks = []
        cited = set()
        source_records: dict[str, dict] = {}
        normalized_sources: dict[str, str] = {}
        integrity: dict[str, bool] = {}
        for claim in list(distinct.values())[:100]:
            source_ids = claim.source_ids
            sources = []
            missing = False
            for source_id in source_ids:
                try:
                    source = source_records.get(source_id)
                    if source is None:
                        if len(source_records) >= 100:
                            raise ScopeDenied("verification source limit exceeded")
                        source = self.repository.read_source(source_id, task_id=ctx.task_id,
                            owner_id=ctx.user_id, project_id=ctx.project_id)
                        source_records[source_id] = source
                        normalized_sources[source_id] = _normalize(source["content"])
                        integrity[source_id] = hashlib.sha256(source["content"].encode()).hexdigest() == source["content_sha256"]
                except ScopeDenied:
                    missing = True
                    continue
                sources.append(source)
                cited.add(source_id)
            if missing or not sources:
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="MISSING",
                    reason="A cited source is missing or outside the task's authorized immutable source store.")
            elif any(source["fetch_status"] != "FETCHED" for source in sources):
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="UNREACHABLE",
                    reason="Every cited snapshot must have been fetched successfully; search snippets are unverified.")
            elif any(not integrity[source["id"]] for source in sources):
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="MISSING",
                    reason="A cited snapshot failed its immutable content hash check.")
            elif claim.confidence == "uncertain":
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="UNRESOLVED",
                    reason="The researcher marked this claim uncertain; deterministic ownership checks cannot resolve it.")
            elif len(_normalize(claim.claim)) >= 20 and all(_normalize(claim.claim) in normalized_sources[source["id"]] for source in sources):
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="QUOTE_SUPPORTED",
                    reason="The complete claim occurs verbatim, allowing whitespace normalization, in every cited fetched snapshot. This checks quotation, not external truth.")
            else:
                check = ClaimCheck(claim=claim.claim, source_ids=source_ids, status="UNSUPPORTED",
                    reason="No deterministic verbatim support for the complete claim in every citation. Paraphrase, inference or contradiction resolution needs further verification.")
            checks.append(check)
        limitations = ["Verification establishes retrieved quote coverage, not semantic truth, source reliability, or absence of conflicting sources."]
        if len(distinct) > 100:
            limitations.append("Claim count exceeds the bounded verification limit; remaining claims are unverified.")
            incomplete = True
        limitations.extend(unknowns[:100])
        if acceptance_criteria:
            limitations.append("Broad acceptance criteria need semantic assessment and remain unresolved in this deterministic verification slice.")
            incomplete = True
        else:
            limitations.append("Goal coverage was not independently assessed; quotation coverage alone cannot establish completion of the user's research objective.")
            incomplete = True
        if not checks:
            limitations.append("No structured, source-linked claims were available in durable specialized child history.")
        if incomplete:
            limitations.append("At least one child handoff was incomplete, failed, or contained unresolved requirements.")
        passed = bool(checks) and not incomplete and not unknowns and all(check.status == "QUOTE_SUPPORTED" for check in checks)
        status = "PASS" if passed else "PARTIAL"
        summary = ("Fetched citations support every returned verbatim quotation; semantic verification remains limited."
                   if passed else "Research verification is partial; unsupported or unresolved claims and coverage limits are recorded in the report.")
        report = self._report(ctx, status, checks, source_records, limitations, acceptance_criteria)
        report_bytes = report.encode("utf-8")
        if len(report_bytes) > 1_900_000:
            limitations.append("Report text exceeded the bounded download limit and was truncated; complete per-claim checks remain in artifact metadata.")
            report_bytes = report_bytes[:1_900_000].decode("utf-8", errors="ignore").encode("utf-8") + b"\n\nReport truncated at the bounded download limit.\n"
        report_digest = hashlib.sha256(report_bytes).hexdigest()
        operation_key = f"research-report:{report_digest}"
        existing = self.repository.artifact_for_operation(ctx, operation_key)
        if existing is None:
            if authority is not None:
                await authority()
            artifact_id = uuid4()
            storage_key, digest = self.storage.write(task_id=ctx.task_id, artifact_id=artifact_id, content=report_bytes)
            await self.authorizer.check_context(ctx)
            if authority is not None:
                await authority()
            existing = self.repository.add_artifact(ctx, artifact_id=artifact_id, operation_key=operation_key,
                storage_key=storage_key, sha256=digest, byte_size=len(report_bytes),
                metadata={"verification_status": status, "claim_checks": [check.model_dump(mode="json") for check in checks],
                          "limitations": limitations, "evidence_ids": sorted(cited)})
        # Content is checked even on resume; a DB reference is not enough if the
        # private storage volume was lost or modified between worker attempts.
        self.storage.read(existing["storage_key"], existing["sha256"])
        return VerificationResult(status=status, summary=summary, checks=tuple(checks),
            evidence_ids=tuple(sorted(cited)), artifact_ids=(existing["id"],), limitations=tuple(limitations))

    def _report(self, ctx, status, checks, sources, limitations, criteria) -> str:
        goal = self.repository.goal(ctx)
        lines = ["# Research report", "", _markdown_text(goal), "", f"Verification: **{status}**", "",
                 "## Claims and citation checks", ""]
        for number, check in enumerate(checks, 1):
            lines.extend([f"### Claim {number}", "", _markdown_text(check.claim), "",
                          f"Check: **{check.status}**", "", _markdown_text(check.reason), ""])
            for source_id in check.source_ids:
                source = sources.get(source_id)
                if source is None:
                    lines.extend([f"Source ID: {_markdown_text(source_id)} — unavailable or unauthorized", ""])
                    continue
                # Angle-wrapped destinations percent-encode Markdown control
                # punctuation so external URLs cannot create extra link markup.
                url = source["url"].replace("<", "%3C").replace(">", "%3E").replace("\n", "%0A")
                lines.extend([f"Source: [{_markdown_text(source['title'])}](<{url}>)", "",
                    f"Source ID: `{source_id}`; retrieved: {source['retrieved_at']}; status: {source['fetch_status']}", "",
                    f"Content SHA-256: `{source['content_sha256']}`", ""])
        if criteria:
            lines.extend(["## Unresolved acceptance criteria", ""])
            for criterion in criteria:
                lines.extend(["- " + _markdown_text(criterion), ""])
        lines.extend(["## Verification limitations", ""])
        lines.extend("- " + _markdown_text(item) for item in limitations)
        return "\n".join(lines) + "\n"

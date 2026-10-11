import type { ApprovalMode } from "@/lib/activity/approvals";
import { api } from "@/lib/task-api";

/** The person's choice for every conversation: "ask" stops before anything that changes something, "auto" never asks. */
export const fetchApprovalMode = (init: RequestInit = {}) => api<{ mode: ApprovalMode }>("/settings/approval-mode", init);

export const saveApprovalMode = (mode: ApprovalMode) =>
  api<{ mode: ApprovalMode }>("/settings/approval-mode", { method: "PUT", body: JSON.stringify({ mode }) });

/** Answers one waiting tool call. Answering twice returns the answer already given. */
export const answerApproval = (approvalId: string, decision: "approve" | "deny") =>
  api<{ id: string; state: string }>(`/tool-approvals/${encodeURIComponent(approvalId)}`, { method: "POST", body: JSON.stringify({ decision }) });

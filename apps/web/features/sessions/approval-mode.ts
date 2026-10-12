"use client";
import { useEffect, useSyncExternalStore } from "react";
import { ShieldCheck, Zap } from "lucide-react";
import type { ApprovalMode } from "@/lib/activity/approvals";
import { createApprovalModeStore, type ApprovalModeState } from "@/lib/approval-mode-store";
import { fetchApprovalMode, saveApprovalMode } from "@/lib/approvals-api";

/** The approval modes and the shared store, without the composer's picker (and its popover), so Settings loads only this. */
export const approvalModes: { id: ApprovalMode; label: string; hint: string; Icon: typeof ShieldCheck }[] = [
  { id: "ask", label: "Ask me", hint: "Asks before it runs a command, changes a file or opens a pull request.", Icon: ShieldCheck },
  { id: "auto", label: "Approve for me", hint: "Does everything without asking.", Icon: Zap },
];

/** One copy for the page: every composer and Settings share it, so switching conversations never reads it again. */
const approvalModeStore = createApprovalModeStore({ fetch: () => fetchApprovalMode(), save: saveApprovalMode });
const idle: ApprovalModeState = { mode: null, status: "idle" };
if (typeof window !== "undefined") for (const event of ["agentforge-session-expired", "agentforge-account-changed"]) window.addEventListener(event, () => approvalModeStore.reset());

/** The approval mode, read on first use. `choose` shows the new mode at once and rejects if it could not be saved. */
export function useApprovalMode() {
  const state = useSyncExternalStore(approvalModeStore.subscribe, approvalModeStore.getSnapshot, () => idle);
  useEffect(() => { approvalModeStore.load(); }, []);
  return { ...state, choose: approvalModeStore.choose, retry: approvalModeStore.load };
}

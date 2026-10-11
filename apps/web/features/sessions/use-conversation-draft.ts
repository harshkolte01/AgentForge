"use client";
import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { accountId, readDraft, writeDraft, type ConversationDraft } from "@/lib/conversation-outbox";
import { validAttachment, attachmentGoal } from "@/lib/attachments";
import type { Attachment } from "@/lib/workspace-api";

const DRAFT_SAVE_MS = 300;

export function useConversationDraft(sessionId: string | null) {
  const [goal, updateGoal] = useState("");
  const [attachments, updateAttachments] = useState<Attachment[]>([]);
  const [identity, setIdentity] = useState<string | null>(null);
  const edited = useRef(false);
  const revision = useRef(0);
  const current = useRef({ goal, attachments });
  const admittedDraft = useRef<{ draftId: string; input: { goal: string; attachments: Attachment[] } } | null>(null);
  useEffect(() => { current.current = { goal, attachments }; }, [goal, attachments]);
  const nextRevision = useCallback(() => { revision.current = Math.max(Date.now(), revision.current + 1); return revision.current; }, []);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const account = await accountId();
      const id = `${account}:${sessionId ?? "new"}`;
      const saved = await readDraft(id);
      if (cancelled) return;
      const admitted = admittedDraft.current;
      const acknowledged = saved && admitted?.draftId === id && attachmentGoal(saved.goal, saved.attachments) === admitted.input.goal &&
        JSON.stringify(saved.attachments) === JSON.stringify(admitted.input.attachments);
      if (saved && !acknowledged && !edited.current && typeof saved.goal === "string" && saved.goal.length <= 8000 && Array.isArray(saved.attachments)) {
        updateGoal(saved.goal);
        updateAttachments(saved.attachments.filter(validAttachment).slice(0, 4));
        revision.current = saved.revision;
      }
      setIdentity(id);
    })().catch(() => { /* Durable send storage still fails visibly on submission. */ });
    return () => { cancelled = true; };
  }, [sessionId]);
  useEffect(() => {
    const admitted = (raw: Event) => {
      const detail = (raw as CustomEvent<{ draftId: string; input: { goal: string; attachments: Attachment[] } }>).detail;
      admittedDraft.current = detail;
      if (detail.draftId === identity && attachmentGoal(current.current.goal, current.current.attachments) === detail.input.goal &&
          JSON.stringify(current.current.attachments) === JSON.stringify(detail.input.attachments)) {
        updateGoal(""); updateAttachments([]);
      }
    };
    window.addEventListener("agentforge-turn-admitted", admitted);
    return () => window.removeEventListener("agentforge-turn-admitted", admitted);
  }, [identity]);
  // Typing is saved after a short pause, not per keystroke; leaving the field, the page or the conversation saves at once.
  const unsaved = useRef<ConversationDraft | null>(null);
  const saveTimer = useRef<number | null>(null);
  const flushDraft = useCallback(() => {
    if (saveTimer.current !== null) { window.clearTimeout(saveTimer.current); saveTimer.current = null; }
    const draft = unsaved.current;
    unsaved.current = null;
    if (draft) void writeDraft(draft).catch(() => {});
  }, []);
  useEffect(() => {
    if (!identity) return;
    // The revision is taken now, at the edit, so a late save can never overwrite a newer one.
    unsaved.current = { id: identity, goal, attachments, revision: nextRevision() };
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(flushDraft, DRAFT_SAVE_MS);
  }, [identity, goal, attachments, nextRevision, flushDraft]);
  useEffect(() => {
    const hidden = () => { if (document.visibilityState === "hidden") flushDraft(); };
    window.addEventListener("pagehide", flushDraft);
    document.addEventListener("visibilitychange", hidden);
    return () => { window.removeEventListener("pagehide", flushDraft); document.removeEventListener("visibilitychange", hidden); flushDraft(); };
  }, [flushDraft]);
  const setGoal = useCallback((value: SetStateAction<string>) => { edited.current = true; updateGoal(value); }, []);
  const setAttachments = useCallback((value: SetStateAction<Attachment[]>) => { edited.current = true; updateAttachments(value); }, []);
  const clearDraft = useCallback(async () => {
    // A save still waiting would bring the sent text back.
    if (saveTimer.current !== null) { window.clearTimeout(saveTimer.current); saveTimer.current = null; }
    unsaved.current = null;
    if (identity) await writeDraft({ id: identity, goal: "", attachments: [], revision: nextRevision() });
  }, [identity, nextRevision]);
  return { goal, attachments, setGoal, setAttachments, clearDraft, flushDraft };
}

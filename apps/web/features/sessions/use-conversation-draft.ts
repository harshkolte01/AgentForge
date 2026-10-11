"use client";
import { useCallback, useEffect, useRef, useState, type SetStateAction } from "react";
import { accountId, readDraft, writeDraft } from "@/lib/conversation-outbox";
import type { Attachment } from "@/lib/workspace-api";

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
      const acknowledged = saved && admitted?.draftId === id && saved.goal.trim() === admitted.input.goal &&
        JSON.stringify(saved.attachments) === JSON.stringify(admitted.input.attachments);
      if (saved && !acknowledged && !edited.current && typeof saved.goal === "string" && saved.goal.length <= 8000 && Array.isArray(saved.attachments)) {
        updateGoal(saved.goal);
        updateAttachments(saved.attachments.filter((item) => typeof item.name === "string" && item.name.length <= 120 && typeof item.content === "string" && item.content.length <= 8000).slice(0, 4));
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
      if (detail.draftId === identity && current.current.goal.trim() === detail.input.goal &&
          JSON.stringify(current.current.attachments) === JSON.stringify(detail.input.attachments)) {
        updateGoal(""); updateAttachments([]);
      }
    };
    window.addEventListener("agentforge-turn-admitted", admitted);
    return () => window.removeEventListener("agentforge-turn-admitted", admitted);
  }, [identity]);
  useEffect(() => {
    if (identity) void writeDraft({ id: identity, goal, attachments, revision: nextRevision() }).catch(() => {});
  }, [identity, goal, attachments, nextRevision]);
  const setGoal = useCallback((value: SetStateAction<string>) => { edited.current = true; updateGoal(value); }, []);
  const setAttachments = useCallback((value: SetStateAction<Attachment[]>) => { edited.current = true; updateAttachments(value); }, []);
  const clearDraft = useCallback(async () => {
    if (identity) await writeDraft({ id: identity, goal: "", attachments: [], revision: nextRevision() });
  }, [identity, nextRevision]);
  return { goal, attachments, setGoal, setAttachments, clearDraft };
}

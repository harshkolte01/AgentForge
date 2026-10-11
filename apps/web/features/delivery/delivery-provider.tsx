"use client";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { DeliveryContext, type DeliveryContextValue, type PanelTab } from "@/features/delivery/delivery-context";
import { DeliveryToaster } from "@/features/delivery/delivery-toaster";
import { useDelivery } from "@/features/delivery/use-delivery";
import { deferredMerge } from "@/lib/deferred-merge";
import { markReady, mergePullRequest, resetDelivery, updateBranch, type MergeMethod } from "@/lib/delivery-api";
import { deliveryMessage, errorCode } from "@/lib/delivery-errors";
import { agentPrompt, deriveHeadline, mergeSentence, unpublishedSummary, type ActionKind } from "@/lib/delivery-status";
import { message } from "@/lib/task-api";
import { useMediaQuery } from "@/lib/use-media-query";

// Only rendered while publishing: its code loads the first time the dialog opens.
const PublishDialog = dynamic(() => import("@/features/delivery/publish-dialog").then((mod) => mod.PublishDialog), { ssr: false });

type Props = {
  sessionId: string | null;
  /** The conversation has a repository: otherwise there is nothing to deliver to. */
  hasRepository: boolean;
  activeTaskId: string | null;
  /** Sends a canned message through the normal send path; rejects when it could not be sent. */
  onAskAgent: (text: string) => Promise<void>;
  children: ReactNode;
};

/**
 * Everything about one conversation's delivery. Its state starts over when the conversation changes, so nothing leaks between
 * conversations, but the workspace it wraps (sidebar, scroller, thread, composer) is never remounted for it: remounting would
 * reset the sidebar's scroll, the composer's focus and the thread's scroll position on every switch.
 */
export function DeliveryProvider({ sessionId, hasRepository, activeTaskId, onAskAgent, children }: Props) {
  const [panelOpen, setPanelOpen] = useState(false);
  const [tab, setTab] = useState<PanelTab>("changes");
  const [publishOpen, setPublishOpen] = useState(false);
  const [confirming, setConfirming] = useState<"continue" | null>(null);
  const [asking, setAsking] = useState(false);
  // Another conversation: what was open or being asked belonged to the last one.
  const [shownSession, setShownSession] = useState(sessionId);
  if (shownSession !== sessionId) {
    setShownSession(sessionId);
    setPanelOpen(false); setTab("changes"); setPublishOpen(false); setConfirming(null); setAsking(false);
  }
  const data = useDelivery({ sessionId, enabled: hasRepository, activeTaskId, wantChanges: panelOpen || publishOpen });
  const { view, changes } = data;
  const desktop = useMediaQuery("(min-width: 1024px)", true);
  const askRef = useRef(onAskAgent);
  useEffect(() => { askRef.current = onAskAgent; }, [onAskAgent]);
  const currentSession = useRef(sessionId);
  useEffect(() => { currentSession.current = sessionId; }, [sessionId]);

  const summary = useMemo(() => unpublishedSummary(view, changes, panelOpen), [view, changes, panelOpen]);
  const headline = useMemo(() => (view?.available ? deriveHeadline(view, summary) : null), [view, summary]);
  const unpublished = summary?.files ?? 0;
  const hasDelivery = !!view && view.available && (view.state !== "none" || !!view.branch);
  const chipVisible = hasRepository && !data.hidden && (view ? (view.available ? hasDelivery || unpublished > 0 : view.unavailable_reason !== "no_repository") : !!data.readFailed);
  const cardVisible = chipVisible && !!view?.available && (hasDelivery || unpublished > 0);

  const snapshot = useSyncExternalStore(deferredMerge.subscribe, deferredMerge.getSnapshot, deferredMerge.getSnapshot);
  const pendingMerge = view?.pull_request ? snapshot.pending.find((item) => item.key === view.pull_request?.url) : undefined;

  const { refreshChanges, run, reportFailure, dismissFailure } = data;
  const openPanel = useCallback((next?: PanelTab) => {
    setTab(next ?? (view?.pull_request && unpublished === 0 ? "pull_request" : "changes"));
    setPanelOpen(true);
    // Opening the panel is a reason to look at the sandbox again.
    refreshChanges();
  }, [view, unpublished, refreshChanges]);
  const closePanel = useCallback(() => setPanelOpen(false), []);
  const openPublish = useCallback(() => setPublishOpen(true), []);
  const closePublish = useCallback(() => setPublishOpen(false), []);

  const ask = useCallback(async (action: "fix_checks" | "resolve" | "address_feedback") => {
    if (!view) return;
    const asked = sessionId;
    // The person may have moved to another conversation meanwhile: its panel and failures are not this one's.
    const here = () => currentSession.current === asked;
    setAsking(true);
    try {
      await askRef.current(agentPrompt(action, view));
      // On a phone the sheet covers the conversation: show the message that was just sent.
      if (!desktop && here()) setPanelOpen(false);
    } catch (error) { if (here()) reportFailure(action, message(error)); }
    finally { if (here()) setAsking(false); }
  }, [sessionId, view, desktop, reportFailure]);

  const perform = useCallback((action: ActionKind) => {
    if (!sessionId || !view) return;
    switch (action) {
      case "create_pull_request": case "push_update": openPublish(); break;
      case "merge": openPanel("pull_request"); break;
      case "ready_for_review": void run("ready_for_review", "ready", (key) => markReady(sessionId, key)); break;
      case "update_branch": void run("update_branch", "update-branch", (key) => updateBranch(sessionId, key)); break;
      case "continue":
        // Discarding unpublished work is confirmed in the panel, so the panel opens to ask.
        if (unpublished > 0) { setConfirming("continue"); openPanel(); }
        else {
          // The summary can lag the sandbox: if the server finds changes that would be lost, ask instead of failing.
          void run("continue", "reset:keep", (key) => resetDelivery(sessionId, key, false), { clearChanges: true }).then((outcome) => {
            if (!outcome.ok && errorCode(outcome.error) === "delivery_unpublished_changes") { dismissFailure(); setConfirming("continue"); openPanel(); }
          });
        }
        break;
      default: void ask(action);
    }
  }, [sessionId, view, unpublished, run, dismissFailure, openPublish, openPanel, ask]);

  const startMerge = useCallback((method: MergeMethod) => {
    const pr = view?.pull_request;
    if (!sessionId || !pr) return;
    const signature = `merge:${pr.url}:${method}:${pr.head_sha}`;
    deferredMerge.start(pr.url, {
      label: mergeSentence(method, pr.number, view.base_branch),
      run: async () => {
        const outcome = await run("merge", signature, (key) => mergePullRequest(sessionId, { idempotency_key: key, method, expected_head_sha: pr.head_sha }));
        // The toast reports it (and Merge starts a new wait); the panel's own retry banner would run it with no undo window.
        if (!outcome.ok) { dismissFailure(); throw outcome.error; }
      },
      failure: (error) => `Couldn't merge #${pr.number}. ${deliveryMessage(error)}`,
      success: `Merged #${pr.number}`,
    });
  }, [sessionId, view, run, dismissFailure]);

  const value: DeliveryContextValue = {
    data, sessionId: sessionId ?? "", summary, headline, chipVisible, cardVisible, panelOpen, tab, setTab, openPanel, closePanel, sheet: panelOpen && !desktop,
    publishOpen, openPublish, closePublish, confirming, setConfirming, perform, startMerge,
    working: asking || !!data.busyAction, pendingMerge,
  };
  return <DeliveryContext value={value}>
    {children}
    {publishOpen && sessionId ? <PublishDialog /> : null}
    <DeliveryToaster />
  </DeliveryContext>;
}

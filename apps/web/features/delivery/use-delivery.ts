"use client";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { discardFile, fetchChanges, fetchDelivery, refreshDelivery, type ChangesView, type DeliveryView } from "@/lib/delivery-api";
import { deliveryMessage, errorCode, reuseKey } from "@/lib/delivery-errors";
import { isOpen } from "@/lib/delivery-status";

/** How often GitHub's state is read: quickly while a pull request is open and looked at, slowly otherwise. */
const POLL_OPEN_MS = 15_000;
const POLL_IDLE_MS = 60_000;
/** A write whose outcome is still being confirmed. */
const POLL_PENDING_MS = 5_000;
const POLL_AFTER_FAILURE_MS = 30_000;
const READ_TIMEOUT_MS = 25_000;
/** The sandbox is only re-read on focus when the last read is older than this. */
const CHANGES_STALE_MS = 15_000;

export type Outcome = { ok: true; view: DeliveryView } | { ok: false; error: unknown };
/** `retryable`: the same action can be run again with the same key; otherwise the person simply does it again. */
export type ActionFailure = { action: string; message: string; code: string | undefined; retryable: boolean };
type Call = (idempotencyKey: string) => Promise<DeliveryView>;
type Last = { name: string; signature: string; call: Call; clearChanges: boolean };

/**
 * One conversation's delivery: the GitHub state (`GET /delivery`, kept fresh by polling) and the sandbox's unpublished changes
 * (`GET /delivery/changes`, read only while its detail is wanted), plus the mutations that move them.
 *
 * A read that fails keeps what was already known and only reports the failure; it never turns into "no pull request". Reads are
 * driven by tick counters in the effects' dependencies, so each one aborts the one before it and nothing sets state synchronously.
 * Every user action gets one idempotency key, reused only when a retry of the same action might already have been acted on.
 */
export function useDelivery({ sessionId, enabled, activeTaskId, wantChanges }: { sessionId: string | null; enabled: boolean; activeTaskId: string | null; wantChanges: boolean }) {
  const active = enabled && !!sessionId;
  const running = !!activeTaskId;
  const [view, setView] = useState<DeliveryView | null>(null);
  const [readFailed, setReadFailed] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [changes, setChanges] = useState<ChangesView | null>(null);
  const [changesError, setChangesError] = useState<string | null>(null);
  const [viewTick, setViewTick] = useState(0);
  const [changesTick, setChangesTick] = useState(0);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [failure, setFailure] = useState<ActionFailure | null>(null);
  const viewRef = useRef<DeliveryView | null>(null);
  const forceRead = useRef(false);
  // Bumped whenever a mutation's answer is applied: a read that began before it must not overwrite it.
  const epoch = useRef(0);
  const changesAt = useRef(0);
  const keys = useRef(new Map<string, string>());
  const last = useRef<Last | null>(null);
  // Everything here belongs to one conversation. Moving to another starts over, and a write still in flight for the last one
  // (it is never cancelled: the server may already have acted on it) is not applied to the new one when it answers.
  const [shownSession, setShownSession] = useState(sessionId);
  if (shownSession !== sessionId) {
    setShownSession(sessionId);
    setView(null); setReadFailed(null); setHidden(false); setRefreshing(false); setChanges(null); setChangesError(null);
    setBusyAction(null); setFailure(null);
  }
  const owner = useRef({ live: true });
  useLayoutEffect(() => {
    const current = { live: true };
    owner.current = current;
    viewRef.current = null; epoch.current += 1; changesAt.current = 0; keys.current.clear(); last.current = null;
    return () => { current.live = false; };
  }, [sessionId]);

  const apply = useCallback((next: DeliveryView) => {
    epoch.current += 1;
    viewRef.current = next;
    setView(next); setReadFailed(null); setHidden(next.unavailable_reason === "no_repository");
    // The answer is shown at once; reading again confirms it (and restarts the polling clock).
    setViewTick((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!active || !sessionId) return;
    const controller = new AbortController();
    const started = epoch.current;
    const forced = forceRead.current;
    forceRead.current = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]);
    let failed = false;
    const current = () => !controller.signal.aborted && epoch.current === started;
    void (forced ? refreshDelivery(sessionId, { signal }) : fetchDelivery(sessionId, { signal }))
      .then((next) => { if (!current()) return; viewRef.current = next; setView(next); setReadFailed(null); setHidden(next.unavailable_reason === "no_repository"); })
      .catch((error: unknown) => {
        if (!current()) return;
        failed = true;
        // This conversation has no repository to deliver to: nothing about delivery is shown at all.
        if (errorCode(error) === "delivery_no_repository") setHidden(true);
        else setReadFailed(deliveryMessage(error));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setRefreshing(false);
        // A hidden tab is not polled: coming back to it reads at once (the focus and visibility listener below).
        if (document.hidden) return;
        const pr = viewRef.current?.pull_request ?? null;
        const delay = failed ? POLL_AFTER_FAILURE_MS : viewRef.current?.pending_operation ? POLL_PENDING_MS : isOpen(pr) ? POLL_OPEN_MS : POLL_IDLE_MS;
        timer = setTimeout(() => setViewTick((value) => value + 1), delay);
      });
    return () => { controller.abort(); clearTimeout(timer); };
    // `running` is a dependency on purpose: a turn starting or finishing is a reason to read again at once.
  }, [active, sessionId, viewTick, running]);

  // Coming back to the tab is a reason to look now, not at the next tick.
  useEffect(() => {
    if (!active) return;
    const look = () => {
      if (document.hidden) return;
      setViewTick((value) => value + 1);
      if (Date.now() - changesAt.current > CHANGES_STALE_MS) setChangesTick((value) => value + 1);
    };
    window.addEventListener("focus", look);
    document.addEventListener("visibilitychange", look);
    return () => { window.removeEventListener("focus", look); document.removeEventListener("visibilitychange", look); };
  }, [active]);

  // The sandbox is read only when its detail is wanted (the panel or the publish dialog is open): the card and chip run on the
  // delivery's own `unpublished` summary. Then again when the delivery changes (a push, a webhook) and when a turn ends. While a
  // turn is running its files are in flux, so the last picture is kept.
  const readable = active && wantChanges && !!view?.available && !running;
  const version = view?.version ?? null;
  useEffect(() => {
    if (!readable || !sessionId) return;
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]);
    void fetchChanges(sessionId, { signal })
      .then((next) => { if (controller.signal.aborted) return; changesAt.current = Date.now(); setChanges(next); setChangesError(null); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setChangesError(deliveryMessage(error)); });
    return () => controller.abort();
  }, [readable, sessionId, version, changesTick]);

  const refresh = useCallback(() => { forceRead.current = true; setRefreshing(true); setViewTick((value) => value + 1); }, []);
  const refreshChanges = useCallback(() => { setChangesError(null); setChangesTick((value) => value + 1); }, []);

  /**
   * Runs one user action. `signature` names the action and what it carries: a retry with the same signature reuses the key
   * only when the first attempt may have been acted on, so the server never does it twice and never replays a refusal.
   */
  const run = useCallback(async (name: string, signature: string, call: Call, options: { clearChanges?: boolean } = {}): Promise<Outcome> => {
    const alive = owner.current;
    const key = keys.current.get(signature) ?? crypto.randomUUID();
    keys.current.set(signature, key);
    last.current = { name, signature, call, clearChanges: !!options.clearChanges };
    setBusyAction(name); setFailure(null);
    try {
      const next = await call(key);
      if (alive.live) keys.current.delete(signature);
      if (alive.live) { apply(next); if (options.clearChanges) setChanges(null); setChangesTick((value) => value + 1); }
      return { ok: true, view: next };
    } catch (error) {
      if (alive.live && !reuseKey(error)) keys.current.delete(signature);
      if (alive.live) {
        setFailure({ action: name, message: deliveryMessage(error), code: errorCode(error), retryable: true });
        // Whatever happened, look again: a refused or half-finished write changes what the screen should say.
        setViewTick((value) => value + 1); setChangesTick((value) => value + 1);
      }
      return { ok: false, error };
    } finally { if (alive.live) setBusyAction((current) => (current === name ? null : current)); }
  }, [apply]);

  const retryFailure = useCallback(() => {
    const again = last.current;
    if (again) void run(again.name, again.signature, again.call, { clearChanges: again.clearChanges });
  }, [run]);
  const dismissFailure = useCallback(() => setFailure(null), []);
  /** For actions that are not run here (asking the agent): their failure is shown in the same place. */
  const reportFailure = useCallback((action: string, message: string) => setFailure({ action, message, code: undefined, retryable: false }), []);

  const discard = useCallback(async (path: string): Promise<boolean> => {
    if (!sessionId) return false;
    const alive = owner.current;
    setBusyAction("discard"); setFailure(null);
    try {
      const next = await discardFile(sessionId, path);
      if (alive.live) { changesAt.current = Date.now(); setChanges(next); setChangesError(null); }
      return true;
    } catch (error) {
      if (alive.live) { setFailure({ action: "discard", message: deliveryMessage(error), code: errorCode(error), retryable: false }); setChangesTick((value) => value + 1); }
      return false;
    } finally { if (alive.live) setBusyAction((current) => (current === "discard" ? null : current)); }
  }, [sessionId]);

  const loading = active && !view && !readFailed && !hidden;
  const changesStatus: "loading" | "ready" | "error" = changes ? "ready" : changesError ? "error" : "loading";
  return { view, changes, loading, hidden, readFailed, refreshing, changesStatus, changesError, busyAction, failure, dismissFailure, reportFailure, retryFailure, refresh, refreshChanges, run, discard };
}

export type DeliveryData = ReturnType<typeof useDelivery>;

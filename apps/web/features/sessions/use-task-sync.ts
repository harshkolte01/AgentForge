"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createBrowserSync } from "@/lib/activity/browser";
import type { SyncController, SyncSnapshot } from "@/lib/activity/sync";
import { createFrameBatcher } from "@/features/sessions/frame-batcher";
import { holdLive, type HeldLive } from "@/features/sessions/timeline-model";
import type { Task } from "@/lib/task-api";

type Snapshot = SyncSnapshot<Task>;
/** What a render sees: the controller's snapshot plus the live text held through the end of the run. */
export type TaskSyncView = { sync: Snapshot; held: HeldLive | null };

/**
 * A store between one recovery controller and React. The controller publishes on every event and every streamed fragment;
 * this republishes at most once per animation frame (never delaying beyond the next one), so a burst of fragments is one
 * render. `attach` and `detach` tie the controller's life to an effect, so StrictMode's mount-unmount-mount creates a fresh
 * controller instead of reviving a stopped one.
 */
function createSyncStore(taskId: string) {
  let view: TaskSyncView = { sync: createBrowserSync({ taskId }).getSnapshot(), held: null }; // an unstarted controller is just the empty state
  let held: HeldLive | null = null;
  let controller: SyncController<Task> | null = null;
  let unsubscribe: (() => void) | null = null;
  const listeners = new Set<() => void>();
  const emit = () => { for (const listener of [...listeners]) listener(); };
  const batcher = createFrameBatcher(() => {
    if (!controller) return;
    view = { sync: controller.getSnapshot(), held };
    emit();
  });
  /** Runs on every change, not once per frame: the end of a run must not slip between two frames and lose the text. */
  const observe = () => {
    if (!controller) return;
    const sync = controller.getSnapshot();
    held = holdLive(held, sync.live, sync.terminal || !!sync.activity.terminal);
    batcher.schedule();
  };
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    getSnapshot: () => view,
    attach(next: SyncController<Task>) {
      controller = next;
      held = null;
      unsubscribe = next.subscribe(observe);
      view = { sync: next.getSnapshot(), held };
      emit();
    },
    detach() {
      batcher.cancel();
      unsubscribe?.();
      unsubscribe = null;
      controller?.stop();
      controller = null;
    },
    retry: () => controller?.retryNow(),
  };
}

/**
 * Follows one task: its activity, live text and sync state, kept current by a recovery controller that is started on mount
 * and stopped (requests aborted, stream closed, timers cleared) on unmount or when the task changes. `savedReady` is whether
 * the saved assistant message is already in the thread; the run only counts as complete once it is.
 */
export function useTaskSync(taskId: string, savedReady: boolean) {
  const [store] = useState(() => createSyncStore(taskId));
  const ready = useRef(savedReady);
  useEffect(() => { ready.current = savedReady; }, [savedReady]);
  useEffect(() => {
    const controller = createBrowserSync({ taskId, savedAnswerReady: () => ready.current });
    store.attach(controller);
    controller.start();
    return () => store.detach();
  }, [store, taskId]);
  // The thread just received the saved answer: look again now instead of waiting for the next scheduled check.
  useEffect(() => { if (savedReady) store.retry(); }, [savedReady, store]);
  const { sync, held } = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return { sync, held, retry: store.retry };
}

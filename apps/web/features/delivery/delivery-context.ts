"use client";
import { createContext, useContext } from "react";
import type { DeliveryData } from "@/features/delivery/use-delivery";
import type { PendingMerge } from "@/lib/deferred-merge";
import type { MergeMethod } from "@/lib/delivery-api";
import type { ActionKind, ChangeSummary, Headline } from "@/lib/delivery-status";

export type PanelTab = "changes" | "pull_request";

export type DeliveryContextValue = {
  data: DeliveryData;
  sessionId: string;
  summary: ChangeSummary;
  /** Null until the first read (or while delivery is not available). */
  headline: Headline | null;
  /** The header chip: present whenever there is something to say about delivery, including why it is unavailable. */
  chipVisible: boolean;
  /** The card under the latest answer: only when there are unpublished changes or a delivery. */
  cardVisible: boolean;
  panelOpen: boolean;
  tab: PanelTab;
  setTab: (tab: PanelTab) => void;
  openPanel: (tab?: PanelTab) => void;
  closePanel: () => void;
  /** The panel covers the page (a modal sheet) rather than sitting beside it. */
  sheet: boolean;
  publishOpen: boolean;
  openPublish: () => void;
  closePublish: () => void;
  /** An action waiting for the person's confirmation before it runs. */
  confirming: "continue" | null;
  setConfirming: (value: "continue" | null) => void;
  perform: (action: ActionKind) => void;
  startMerge: (method: MergeMethod) => void;
  /** A mutation or a request to the agent is in flight. */
  working: boolean;
  /** The merge waiting out its undo window for the open pull request, if any. */
  pendingMerge: PendingMerge | undefined;
};

export const DeliveryContext = createContext<DeliveryContextValue | null>(null);
/** Null outside a provider (nothing about delivery is then drawn). */
export const useDeliveryContext = () => useContext(DeliveryContext);

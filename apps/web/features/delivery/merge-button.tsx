"use client";
import { useState } from "react";
import { GitMerge, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { smallButton } from "@/features/delivery/styles";
import { useNow } from "@/features/delivery/use-now";
import { deferredMerge, secondsLeft } from "@/lib/deferred-merge";
import type { MergeMethod } from "@/lib/delivery-api";
import { gateAction, mergeSentence, pickMergeMethod } from "@/lib/delivery-status";

const METHOD_KEY = "agentforge.delivery.merge-method";
const remembered = () => { try { return localStorage.getItem(METHOD_KEY); } catch { return null; } };
const remember = (method: string) => { try { localStorage.setItem(METHOD_KEY, method); } catch { /* The choice then lasts for this visit. */ } };
const shortLabels: Record<MergeMethod, string> = { squash: "Squash", merge: "Merge commit", rebase: "Rebase" };

/**
 * Merge, with a method the repository allows. The click does not merge at once: it starts a five-second wait with an Undo (also
 * Ctrl/Cmd+Z), because merging cannot be taken back. The button's tooltip says exactly what it will do.
 */
export function MergeButtons() {
  const delivery = useDeliveryContext();
  const [chosen, setChosen] = useState<string | null>(remembered);
  const pending = delivery?.pendingMerge;
  const now = useNow(!!pending, 250);
  const view = delivery?.data.view;
  const pr = view?.pull_request;
  if (!delivery || !view || !pr) return null;
  const method = pickMergeMethod(pr.merge_methods, chosen);
  const gate = gateAction("merge", view, delivery.data.changes);
  const disabled = !gate.enabled || !method;
  const size = `min-h-10 px-3 sm:min-h-8 ${smallButton}`;
  return <div className="flex items-center gap-1.5" data-merge-control>
    {pending ? (pending.phase === "waiting"
      ? <Button type="button" variant="outline" className={size} onClick={() => deferredMerge.cancel(pr.url)}><Undo2 />Undo<span aria-hidden="true" className="tabular-nums text-muted-foreground">({secondsLeft(pending.endsAt, now)}s)</span></Button>
      : <Button type="button" className={size} disabled><GitMerge />Merging…</Button>)
      : <Button type="button" className={size} disabled={disabled} title={method ? mergeSentence(method, pr.number, view.base_branch) : undefined} onClick={() => { if (method) delivery.startMerge(method); }}><GitMerge />Merge</Button>}
    {!pending && pr.merge_methods.length > 1 ? <select aria-label="Merge method" value={method ?? ""} onChange={(event) => { setChosen(event.target.value); remember(event.target.value); }}
      className="h-10 rounded-lg border-0 bg-muted px-2 text-[13px] text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 sm:h-8 pointer-coarse:h-11">
      {pr.merge_methods.map((option: MergeMethod) => <option key={option} value={option}>{shortLabels[option]}</option>)}
    </select> : null}
  </div>;
}

/** What the merge is doing, or why it cannot: shown only when there is something to say. */
export function MergeNote() {
  const delivery = useDeliveryContext();
  const pending = delivery?.pendingMerge;
  const view = delivery?.data.view;
  if (!delivery || !view?.pull_request) return null;
  const gate = gateAction("merge", view, delivery.data.changes);
  const text = pending ? (pending.phase === "waiting" ? `${pending.label} in a moment. Undo with the button or Ctrl/⌘ Z.` : `${pending.label}…`) : !gate.enabled ? gate.reason : null;
  return text ? <p role="status" className="text-xs leading-5 text-muted-foreground">{text}</p> : null;
}

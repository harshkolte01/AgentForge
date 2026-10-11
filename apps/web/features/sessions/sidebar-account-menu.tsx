"use client";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ChevronUp, LogOut, Settings } from "lucide-react";
import { useWorkspaceIdentity } from "@/components/session-boundary";

export function SidebarAccountMenu() {
  const identity = useWorkspaceIdentity();
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const focusFrame = useRef<number | null>(null);
  const firstFocus = useRef<"first" | "last">("first");
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, bottom: 60, width: 216 });
  function measure() {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect) setPosition({ left: rect.left, bottom: window.innerHeight - rect.top + 6, width: rect.width });
  }
  function items() { return Array.from(popup.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? []); }
  function close(returnFocus = true) { if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current); popup.current?.hidePopover(); if (returnFocus) trigger.current?.focus(); }
  function show(where: "first" | "last") { firstFocus.current = where; measure(); popup.current?.showPopover(); }
  useEffect(() => {
    if (!open || !trigger.current) return;
    const observer = new ResizeObserver(measure);
    observer.observe(trigger.current);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [open]);
  useEffect(() => () => { if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current); }, []);
  function menuKeys(event: KeyboardEvent<HTMLDivElement>) {
    const controls = items(), index = controls.indexOf(document.activeElement as HTMLElement);
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key === "Tab") { close(); return; }
    let next = -1;
    if (event.key === "ArrowDown") next = (index + 1) % controls.length;
    else if (event.key === "ArrowUp") next = (index - 1 + controls.length) % controls.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = controls.length - 1;
    else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      next = controls.findIndex((_, offset) => controls[(index + offset + 1) % controls.length]?.textContent?.trim().toLowerCase().startsWith(event.key.toLowerCase()));
      if (next >= 0) next = (index + next + 1) % controls.length;
    }
    if (next >= 0 && controls[next]) { event.preventDefault(); controls[next].focus(); }
  }
  if (!identity) return null;
  const name = identity.session.user?.display_name ?? "Workspace";
  const actionClass = "flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-left text-xs outline-none hover:bg-muted focus:bg-muted disabled:opacity-50 sm:min-h-9";
  return <div className="min-w-0">
    <button ref={trigger} id={`${id}-trigger`} type="button" popoverTarget={id} aria-haspopup="menu" aria-expanded={open} aria-controls={id} aria-label={`Account options for ${name}`} disabled={identity.busy} onClick={() => { firstFocus.current = "first"; measure(); }} onKeyDown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(event.key === "ArrowUp" ? "last" : "first"); } else if (event.key === "Escape" && popup.current?.matches(":popover-open")) { event.preventDefault(); event.stopPropagation(); close(); } }} className="flex min-h-11 w-full min-w-0 items-center gap-2 rounded-lg px-2 text-left outline-none hover:bg-sidebar-accent focus-visible:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50 sm:min-h-10">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary text-[11px] font-medium" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span><span className="min-w-0 flex-1 truncate text-xs font-medium">{name}</span><ChevronUp className={`size-3.5 shrink-0 text-muted-foreground ${open ? "rotate-180" : ""}`} aria-hidden="true" />
    </button>
    <div ref={popup} id={id} popover="auto" role="menu" aria-labelledby={`${id}-trigger`} style={{ position: "fixed", ...position, top: "auto", right: "auto", margin: 0 }} className="rounded-lg border bg-popover p-1 text-popover-foreground shadow-md" onKeyDown={menuKeys} onToggle={(event) => {
      const next = (event.nativeEvent as ToggleEvent).newState === "open";
      setOpen(next);
      if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
      if (next) focusFrame.current = requestAnimationFrame(() => { const controls = items(); (firstFocus.current === "last" ? controls.at(-1) : controls[0])?.focus(); });
      else if (popup.current?.contains(document.activeElement)) trigger.current?.focus();
    }}>
      <p className="px-2 pb-2 pt-1 text-[11px] text-muted-foreground">{identity.session.mode === "github_app" ? "GitHub account" : "Local access"}</p>
      <Link href="/settings" role="menuitem" tabIndex={-1} className={actionClass} onClick={() => close(false)}><Settings className="size-3.5" aria-hidden="true" />Settings</Link>
      <button type="button" role="menuitem" tabIndex={-1} className={actionClass} disabled={identity.busy} onClick={() => { close(false); void identity.logout(); }}><LogOut className="size-3.5" aria-hidden="true" />Sign out</button>
    </div>
  </div>;
}

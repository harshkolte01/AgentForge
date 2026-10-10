"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

/** Icon button that copies text and briefly confirms. Clipboard failures stay silent; nothing else depends on them. */
export function CopyButton({ text, label = "Copy", showLabel = false, className = "" }: { text: string; label?: string; showLabel?: boolean; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true); clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch { /* Clipboard access can be denied; the text stays selectable. */ }
  }
  return <button type="button" onClick={() => void copy()} aria-label={copied ? "Copied" : label} title={copied ? "Copied" : label} className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg text-xs text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none ${showLabel ? "h-7 px-2" : "size-8"} ${className}`}>
    {copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
    {showLabel ? <span>{copied ? "Copied" : label}</span> : null}
    <span role="status" className="sr-only">{copied ? "Copied to clipboard" : ""}</span>
  </button>;
}

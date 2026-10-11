"use client";
import { useEffect, useState } from "react";
import { CopyButton } from "@/features/sessions/copy-button";
import { highlightCode, tokenClass, type TokenNode } from "@/features/sessions/highlight";
import { highlightEligibility } from "@/features/sessions/highlight-policy";

function Tokens({ nodes }: { nodes: readonly TokenNode[] }) {
  return nodes.map((node, index) => typeof node === "string" ? node : <span key={index} className={tokenClass(node.className)}><Tokens nodes={node.children} /></span>);
}

/**
 * A fenced code block: language label, copy control, and its own scroll container so a long line never widens the page.
 * While the answer is still streaming the block is plain text; once it is complete it is highlighted if it is small enough and
 * its language has a grammar. Highlighting loads on demand and any failure leaves the plain text in place.
 */
export function CodeBlock({ code, language, streaming = false }: { code: string; language?: string; streaming?: boolean }) {
  const eligible = highlightEligibility({ code, language, streaming });
  const grammar = eligible.ok ? eligible.language : null;
  const key = grammar ? `${grammar}\u0000${code}` : null;
  const [result, setResult] = useState<{ key: string; nodes: TokenNode[] } | null>(null);
  useEffect(() => {
    if (!grammar || key === null) return;
    let cancelled = false;
    void highlightCode(code, grammar).then((nodes) => { if (!cancelled && nodes) setResult({ key, nodes }); });
    return () => { cancelled = true; };
  }, [code, grammar, key]);
  const nodes = result && result.key === key ? result.nodes : null;
  return <div className="my-4 min-w-0 max-w-full overflow-hidden rounded-xl border bg-muted/40" data-code-block data-highlighted={nodes ? "true" : "false"}>
    <div className="flex min-h-9 items-center justify-between border-b bg-muted/60 pl-3.5 pr-1.5 text-xs text-muted-foreground">
      <span className="font-mono">{language ?? "text"}</span>
      <CopyButton text={code} label="Copy code" showLabel />
    </div>
    <pre className="max-w-full overflow-x-auto p-3.5 font-mono text-[13px] leading-6" tabIndex={0} role="region" aria-label={`${language ?? "Code"} block`}><code>{nodes ? <Tokens nodes={nodes} /> : code}</code></pre>
  </div>;
}

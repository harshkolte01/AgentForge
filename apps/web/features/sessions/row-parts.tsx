import Image from "next/image";
import { useState } from "react";
import { McpMark } from "@/features/plugins/plugin-logo";
import { pluginLogos } from "@/lib/plugin-logos";
import { Ban, Check, ChevronRight, CircleAlert, CircleHelp, Square, TriangleAlert } from "lucide-react";
import { pluginName } from "@/lib/activity/presentation";
import type { StatusIcon, Tone } from "@/lib/activity/types";

/** The parts every row of the activity timeline is made of, shared by the timeline and the subagent cards. */

/**
 * Calls already shown while the model was writing them (by `callId`). When such a call starts, its real row takes the draft's
 * place: it has already entered, so it must not fade and rise in a second time.
 */
export const draftedCalls = new Set<string>();

/** Compact service branding from the same local assets as the Plugins page. */
function PluginCallLogo({ tool }: { tool: string }) {
  const slug = tool.slice(0, tool.indexOf("__"));
  // Connecting a service more than once appends a numeric suffix to its catalog slug.
  const key = Object.hasOwn(pluginLogos, slug) ? slug : slug.replace(/-(?:[2-9]|[1-9]\d+)$/, "");
  const logo = Object.hasOwn(pluginLogos, key) ? pluginLogos[key] : undefined;
  const [failed, setFailed] = useState(false);
  if (!logo || failed) return <McpMark />;
  return <Image src={`/plugins/${logo.file}`} alt="" width={16} height={16} unoptimized draggable={false}
    onError={() => setFailed(true)}
    className={`size-4 object-contain ${logo.dark ? "dark:brightness-0 dark:invert" : ""}`} />;
}

/** The glyph in front of a row. Always paired with the row's words, so colour and shape are never the only signal. */
export function Glyph({ icon, tone, current, tool }: { icon: StatusIcon; tone: Tone; current: boolean; tool?: string }) {
  // Plugin identity replaces only the completed check; lifecycle and exception signals retain priority.
  const plugin = icon === "success" && !!tool && pluginName(tool) !== null;
  return <span className="flex size-4 shrink-0 items-center justify-center" aria-hidden="true" data-icon={plugin ? "plugin" : icon}>
    {icon === "running" ? (current ? <span className="thinking-orb" /> : <span className="size-1.5 rounded-full bg-current opacity-50" />)
      : icon === "failed" ? <TriangleAlert className={`size-3.5 ${tone === "danger" ? "text-destructive" : ""}`} />
        : icon === "warning" ? <CircleAlert className="size-3.5" />
          : icon === "blocked" ? <Ban className="size-3.5" />
            : icon === "stopped" ? <Square className="size-3" />
              : icon === "unknown" ? <CircleHelp className="size-3.5" />
                : plugin ? <PluginCallLogo key={tool} tool={tool!} /> : <Check className="size-3.5" />}
  </span>;
}

export const chevron = (open: boolean) => <ChevronRight className={`size-3.5 shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} aria-hidden="true" />;
export const toggleClass = "outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none";

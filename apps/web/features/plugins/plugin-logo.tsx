import Image from "next/image";
import { pluginLogos } from "@/lib/plugin-logos";

/**
 * The Model Context Protocol mark (the protocol every plugin speaks), drawn in the current text colour. Used for servers a person
 * adds themselves.
 */
export function McpMark({ className = "size-4" }: { className?: string }) {
  return <svg viewBox="0 0 24 24" fill="currentColor" fillRule="evenodd" aria-hidden="true" focusable="false" className={className}>
    <path d="M15.688 2.343a2.588 2.588 0 00-3.61 0l-9.626 9.44a.863.863 0 01-1.203 0 .823.823 0 010-1.18l9.626-9.44a4.313 4.313 0 016.016 0 4.116 4.116 0 011.204 3.54 4.3 4.3 0 013.609 1.18l.05.05a4.115 4.115 0 010 5.9l-8.706 8.537a.274.274 0 000 .393l1.788 1.754a.823.823 0 010 1.18.863.863 0 01-1.203 0l-1.788-1.753a1.92 1.92 0 010-2.754l8.706-8.538a2.47 2.47 0 000-3.54l-.05-.049a2.588 2.588 0 00-3.607-.003l-7.172 7.034-.002.002-.098.097a.863.863 0 01-1.204 0 .823.823 0 010-1.18l7.273-7.133a2.47 2.47 0 00-.003-3.537z" />
    <path d="M14.485 4.703a.823.823 0 000-1.18.863.863 0 00-1.204 0l-7.119 6.982a4.115 4.115 0 000 5.9 4.314 4.314 0 006.016 0l7.12-6.982a.823.823 0 000-1.18.863.863 0 00-1.204 0l-7.119 6.982a2.588 2.588 0 01-3.61 0 2.47 2.47 0 010-3.54l7.12-6.982z" />
  </svg>;
}

const sizes = {
  xs: { tile: "size-8 rounded-lg", mark: "size-4", text: "text-[11px]" },
  sm: { tile: "size-9 rounded-lg", mark: "size-4", text: "text-xs" },
  md: { tile: "size-11 rounded-xl", mark: "size-5", text: "text-sm" },
  lg: { tile: "size-14 rounded-2xl", mark: "size-7", text: "text-lg" },
} as const;

/**
 * A plugin's logo: the mark its owner publishes (see lib/plugin-logos.ts), on a plain tile. `pluginKey` is the catalog key, or
 * "custom" for a server the person added, which gets the MCP mark. A plugin with no logo gets the first letter of its name.
 */
export function PluginLogo({ pluginKey, name, size = "md", className = "" }: { pluginKey: string; name: string; size?: keyof typeof sizes; className?: string }) {
  const look = sizes[size];
  const logo = pluginLogos[pluginKey];
  const tile = `grid shrink-0 select-none place-items-center overflow-hidden border text-foreground ${look.tile} ${className}`;
  if (logo?.fit === "icon") {
    // An app icon: it has its own background and shape, so it fills the tile.
    return <span aria-hidden="true" className={`${tile} bg-muted`}><Image src={`/plugins/${logo.file}`} alt="" width={96} height={96} unoptimized draggable={false} className="size-full object-cover" /></span>;
  }
  return <span aria-hidden="true" className={`${tile} bg-card`}>
    {logo ? <Image src={`/plugins/${logo.file}`} alt="" width={64} height={64} unoptimized draggable={false} className={`size-[56%] object-contain ${logo.dark ? "dark:brightness-0 dark:invert" : ""}`} />
      : pluginKey === "custom" ? <McpMark className={look.mark} />
        : <span className={`font-semibold uppercase ${look.text}`}>{[...name][0] ?? "?"}</span>}
  </span>;
}

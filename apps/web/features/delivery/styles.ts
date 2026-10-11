import type { Tone } from "@/lib/delivery-status";

/** The workspace is monochrome; delivery state is the one place colour carries meaning, so it is kept to these few tones. */
export const toneDot: Record<Tone, string> = {
  green: "bg-emerald-500",
  yellow: "bg-amber-500",
  red: "bg-red-500",
  purple: "bg-violet-500",
  muted: "bg-muted-foreground/50",
};
export const toneText: Record<Tone, string> = {
  green: "text-emerald-700 dark:text-emerald-400",
  yellow: "text-amber-700 dark:text-amber-400",
  red: "text-red-700 dark:text-red-400",
  purple: "text-violet-700 dark:text-violet-400",
  muted: "text-muted-foreground",
};

export const linkClass = "rounded underline underline-offset-4 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60";
export const smallButton = "pointer-coarse:min-h-11";
export const inputClass = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50 pointer-coarse:min-h-11";
export const labelClass = "text-xs font-medium text-foreground";

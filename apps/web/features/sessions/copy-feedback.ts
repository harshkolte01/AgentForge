/**
 * Copy-to-clipboard feedback as data: what the control says in each state, and the copy itself. Clipboard access can be
 * denied (no permission, an insecure context, a browser without it), and a silent failure reads as a broken button, so the
 * failure is stated briefly, announced, and clears by itself.
 */
export type CopyState = "idle" | "copied" | "failed";

export const COPIED_MS = 1800;
export const FAILED_MS = 3000;

export type CopyLabels = {
  /** The accessible name and tooltip of the button. */
  name: string;
  /** The visible text next to the icon, when the button shows one. */
  visible: string;
  /** What the polite status region announces; empty when nothing changed. */
  status: string;
  /** How long before the state returns to idle; null for idle. */
  clearAfter: number | null;
};

export function copyLabels(state: CopyState, label: string): CopyLabels {
  switch (state) {
    case "copied": return { name: "Copied", visible: "Copied", status: "Copied to clipboard", clearAfter: COPIED_MS };
    case "failed": return { name: "Couldn't copy", visible: "Couldn't copy", status: "Couldn't copy to the clipboard", clearAfter: FAILED_MS };
    default: return { name: label, visible: label, status: "", clearAfter: null };
  }
}

export type ClipboardLike = { writeText(text: string): Promise<void> } | undefined | null;

/** Writes to the clipboard; false when it is missing or refuses. Never throws. */
export async function copyToClipboard(text: string, clipboard: ClipboardLike = typeof navigator === "undefined" ? undefined : navigator.clipboard): Promise<boolean> {
  if (!clipboard || typeof clipboard.writeText !== "function") return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

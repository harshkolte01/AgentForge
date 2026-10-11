/**
 * The files an answer produced, as plain data. Everything comes from owner-scoped server records; nothing here invents a link or a file.
 * Free of React so the rules are node-tested.
 */
import type { Artifact } from "@/lib/task-api";

// ---------------------------------------------------------------------------------------------------------------------
// Deliverables

export type DeliverableView = {
  id: string;
  filename: string;
  size: string;
  /** The owner-authorized download route. Null when the record does not look like one of ours (nothing is linked). */
  href: string | null;
};

const safeId = /^[A-Za-z0-9_-]{1,64}$/;

/** "812 B", "4 KB", "1.3 MB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${bytes < 10 * 1024 ? (bytes / 1024).toFixed(1).replace(/\.0$/, "") : Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace(/\.0$/, "")} MB`;
}

/** The download route of one artifact, or null when either id is not a plain identifier. */
export function artifactHref(taskId: string, artifactId: string): string | null {
  return safeId.test(taskId) && safeId.test(artifactId) ? `/api/v1/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}` : null;
}

export function deliverableViews(taskId: string, artifacts: readonly Pick<Artifact, "id" | "filename" | "byte_size">[]): DeliverableView[] {
  return artifacts.map((artifact) => ({
    id: artifact.id,
    filename: artifact.filename.trim() || "Download",
    size: formatBytes(artifact.byte_size),
    href: artifactHref(taskId, artifact.id),
  }));
}

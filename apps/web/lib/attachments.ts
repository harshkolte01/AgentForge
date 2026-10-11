import { friendlyError } from "@/lib/task-api";
import type { Attachment } from "@/lib/workspace-api";

export const MAX_ATTACHMENTS = 4;
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;
export const ATTACHMENT_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.md,.markdown,.mdx,.csv,.tsv,.json,.jsonl,.py,.js,.jsx,.ts,.tsx,.html,.css,.yaml,.yml,.xml,.log,.sql,.sh,.rs,.go,.java,.c,.cpp,.h,.toml,.ini";
const extensions = new Set(ATTACHMENT_ACCEPT.split(","));

export function validateFiles(files: readonly Pick<File, "name" | "size">[], count: number): void {
  if (files.length + count > MAX_ATTACHMENTS) throw new Error("Attach up to four files per message.");
  for (const file of files) {
    if (!file.size) throw new Error(`${file.name} is empty.`);
    if (file.size > MAX_UPLOAD_BYTES) throw new Error(`${file.name} is too large. Choose a file up to 8 MB.`);
    if (!file.name || file.name.length > 120) throw new Error("Keep filenames below 120 characters.");
    const dot = file.name.lastIndexOf(".");
    if (![".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(file.name.slice(dot).toLowerCase()) && file.size > 512 * 1024) throw new Error(`${file.name}: text and source files can be up to 512 KB.`);
    if (dot < 0 || !extensions.has(file.name.slice(dot).toLowerCase())) throw new Error(`${file.name}: use a PDF, still image, or text/source file.`);
  }
}

export function validAttachment(value: unknown): value is Attachment {
  if (!value || typeof value !== "object") return false;
  const item = value as Attachment;
  if (typeof item.name !== "string" || !item.name.length || item.name.length > 120 || typeof item.content !== "string" || !item.content.length || item.content.length > 8000) return false;
  if (!item.upload_id) return !item.upload_sha256 && !item.media_type && !item.byte_size && item.page_count === undefined;
  return /^[0-9a-f-]{36}$/.test(item.upload_id) && /^[0-9a-f]{64}$/.test(item.upload_sha256 ?? "") &&
    ["image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf", "text/plain"].includes(item.media_type ?? "") &&
    typeof item.byte_size === "number" && item.byte_size > 0 && item.byte_size <= MAX_UPLOAD_BYTES &&
    (item.media_type === "application/pdf" ? Number.isInteger(item.page_count) && item.page_count! >= 1 && item.page_count! <= 20 : item.page_count === undefined);
}

/** A known code's sentence, a sentence the server wrote itself, or a generic one: never a raw code like `session_required`. */
function uploadError(detail: unknown, name: string): string {
  if (typeof detail === "string") {
    const known = friendlyError(detail);
    if (known) return known;
    if (/\s/.test(detail.trim())) return detail;
  }
  return `Couldn’t attach ${name}. Try again.`;
}

export async function uploadAttachment(file: File, signal: AbortSignal): Promise<Attachment> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/attachments?name=${encodeURIComponent(file.name)}`, {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin", signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    // No answer at all: a sentence, as `api()` gives, instead of the browser's "Failed to fetch".
    throw new Error(`Couldn’t attach ${file.name}: AgentForge could not be reached. Check your connection, then try again.`);
  }
  if (response.status === 401) window.dispatchEvent(new Event("agentforge-session-expired"));
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new Error(uploadError(value?.detail, file.name));
  if (!validAttachment(value)) throw new Error(`Couldn’t read ${file.name}. Try attaching it again.`);
  return value;
}

/** Only real clipboard files are attachments. HTML image URLs are never fetched. */
export function transferredFiles(transfer: DataTransfer): File[] {
  const files = Array.from(transfer.items ?? []).filter((item) => item.kind === "file").flatMap((item) => {
    const file = item.getAsFile(); return file ? [file] : [];
  });
  return files.length ? files : Array.from(transfer.files ?? []);
}

export function attachmentGoal(goal: string, attachments: readonly Attachment[]): string {
  return goal.trim() || (attachments.length ? "Please review the attached files." : "");
}

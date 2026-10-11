import type { AttachmentView, MessageActivity, Task } from "@/lib/task-api";
export type TaskMode = "research" | "general" | "coding";
export type RepositorySelection = { repo_id: string; ref: string };
/** What the sidebar shows beside a conversation (see `lib/conversation-status.ts`). Absent from older servers. */
export type ConversationStatus = "idle" | "running" | "waiting_approval" | "failed" | "unread";
export type ConversationSession = { id: string; project_id: string; title: string; mode: TaskMode; repository: RepositorySelection | null; archived: boolean; active_task_id: string | null; continuation_task_id: string | null; last_sequence: number; created_at: string; updated_at: string; queue_paused?: boolean; queued_count?: number; revision?: number; status?: ConversationStatus; read_sequence?: number };
export type MessageStep = { tool: string; status: "done" | "failed" };
export type ConversationMessage = { id: string; session_id: string; sequence: number; role: "user" | "assistant"; content: string; task_id: string | null; created_at: string; steps?: MessageStep[];
  /** What the work behind an assistant message amounted to (exact counts, note-only activity). Absent from older servers. */
  activity?: MessageActivity | null;
  /** A user message's attachments as displayed: names and sizes, never contents. Absent from older servers. */
  attachments?: AttachmentView[]; turn_id?: string | null; turn_state?: string | null };
export type TurnResult = { session_id: string; user_message: ConversationMessage; task: Task | null; assistant_message: ConversationMessage | null; replayed: boolean; turn_id?: string; state?: string; queue_position?: number | null; error_code?: string | null };
export type Attachment = { name: string; content: string; upload_id?: string; upload_sha256?: string; media_type?: string; byte_size?: number; page_count?: number };
export type TurnInput = { routing: "auto"; repository: RepositorySelection | null; idempotency_key: string; goal: string; allowed_web_hosts: string[]; attachments: Attachment[] };
export type WorkerPresence = { status: "online" | "offline"; last_seen_at: string | null };
export type AuthMode = "disabled" | "local_single_user" | "github_app";
/** Fields marked optional are absent from older servers: read them defensively. */
export type WorkspaceConfiguration = { conversation_queue_enabled?: boolean; coding_repository_access?: string; per_task_cost_limit_usd?: string | number; research_configured: boolean; coding_configured: boolean; general_configured?: boolean; general_capabilities?: string[]; general_execution?: string; general_sandbox_execution?: string; coding_missing_settings: string[]; missing_settings: string[]; general_missing_settings?: string[]; tracing_configured: boolean; qualification: string; configured_model?: string | null; configured_provider?: string | null; model?: string | null; provider?: string | null;
  worker?: WorkerPresence | null; github_writes_enabled?: boolean; auth_mode?: AuthMode; web_research_configured?: boolean };

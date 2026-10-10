import type { CodingSpec, Task } from "@/lib/task-api";
export type TaskMode = "research" | "general" | "coding";
export type ConversationSession = { id: string; project_id: string; title: string; mode: TaskMode; archived: boolean; active_task_id: string | null; last_sequence: number; created_at: string; updated_at: string };
export type ConversationMessage = { id: string; session_id: string; sequence: number; role: "user" | "assistant"; content: string; task_id: string | null; created_at: string };
export type TurnResult = { session_id: string; user_message: ConversationMessage; task: Task; replayed: boolean };
export type Attachment = { name: string; content: string };
export type TurnInput = { continue_from_task_id?: string; idempotency_key: string; goal: string; kind: TaskMode; coding?: CodingSpec; max_cost_usd: string; max_wall_time_sec: number; allowed_web_hosts: string[]; attachments: Attachment[]; agent_key?: string; agent_version?: number };
export type WorkspaceConfiguration = { coding_repository_access?: string; per_task_cost_limit_usd?: string | number; research_configured: boolean; coding_configured: boolean; general_configured?: boolean; general_capabilities?: string[]; general_execution?: string; general_sandbox_execution?: string; coding_missing_settings: string[]; missing_settings: string[]; general_missing_settings?: string[]; tracing_configured: boolean; qualification: string; configured_model?: string | null; configured_provider?: string | null; model?: string | null; provider?: string | null };

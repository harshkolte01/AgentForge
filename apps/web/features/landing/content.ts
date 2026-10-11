/** Public product copy. Keep implementation details in workspace help and operator docs. */
export const sections = [
  { id: "how-it-works", label: "How it works" },
  { id: "possibilities", label: "What you can do" },
  { id: "faq", label: "FAQ" },
] as const;
export type SectionId = (typeof sections)[number]["id"];

export const steps = [
  { title: "Describe your task", body: "Start with a question, a bug, or an idea. Add files or connect a repository when you need to." },
  { title: "Work through it together", body: "AgentForge researches, writes code, and runs tests. Ask follow-up questions as the work takes shape." },
  { title: "Review the result", body: "Inspect the changes, give feedback, and decide what comes next." },
] as const;

export const faqs = [
  { q: "Do I need to connect a repository?", a: "No. Start with research, a question, or files you want to work with. Connect a GitHub repository when your task needs one." },
  { q: "Can I review actions before they run?", a: "Yes. In Ask me mode, you approve commands, file changes, and pull requests before they run. Choose Approve for me to let the agent proceed without those prompts. You can stop a run from the conversation." },
  { q: "Can I come back to my work?", a: "Yes. Your conversations are saved, so you can reopen a chat and continue where you left off." },
] as const;

/** Illustrative requests, not claims of completed work. */
export const examples = [
  { title: "Understand a codebase", prompt: "Walk me through how sign-in works in this repository." },
  { title: "Build something new", prompt: "Build a CSV-to-JSON tool and add tests for it." },
  { title: "Get a fix moving", prompt: "Find out why these tests are failing and propose a fix." },
] as const;

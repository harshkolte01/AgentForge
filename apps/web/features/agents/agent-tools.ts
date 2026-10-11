/** What each tool a custom agent may be given is called, everywhere an agent's tools are shown. */
export const toolLabels: Record<string, string> = {
  read_task_input: "Read task inputs",
  read_research_source: "Read saved sources",
  web_search: "Search the web",
  fetch_webpage: "Fetch source pages",
};

/** The tools a person can switch on or off in the editor (reading task inputs is always kept). */
export const editableTools: readonly { key: string; hint: string }[] = [
  { key: "read_research_source", hint: "Reopen pages the agent has already saved." },
  { key: "web_search", hint: "Look things up on the web." },
  { key: "fetch_webpage", hint: "Open a page and read it before relying on it." },
];

export function toolLabel(tool: string): string {
  return toolLabels[tool] ?? tool.replaceAll("_", " ");
}

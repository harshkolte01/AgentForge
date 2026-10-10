import { redirect } from "next/navigation";

import { routes, uuidPattern } from "@/lib/routes";

/** `/workspace` was the original conversation URL. Keep old bookmarks and OAuth return links working. */
export default async function LegacyWorkspacePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const session = typeof query.session === "string" && uuidPattern.test(query.session) ? query.session.toLowerCase() : null;
  const destination = session ? routes.conversation(session) : routes.newConversation;
  redirect(typeof query.auth_error === "string" ? `${destination}?auth_error=${encodeURIComponent(query.auth_error.slice(0, 64))}` : destination);
}

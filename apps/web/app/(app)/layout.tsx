import type { Metadata } from "next";
import { SessionBoundary } from "@/components/session-boundary";

// A private workspace: never worth indexing.
export const metadata: Metadata = { robots: { index: false, follow: false } };

/** One session check for the whole signed-in app: moving between chat and settings never re-reads it or shows a loading screen. */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <SessionBoundary>{children}</SessionBoundary>;
}

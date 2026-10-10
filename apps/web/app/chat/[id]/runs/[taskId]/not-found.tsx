import Link from "next/link";

export default function RunNotFound() {
  return <div className="space-y-3"><h1 className="text-2xl font-semibold tracking-tight">Run not found</h1><p className="text-sm leading-6 text-muted-foreground">This link is not a valid run. It may have been copied incorrectly.</p><Link href="/chat" className="inline-flex text-sm font-medium underline underline-offset-4 focus-visible:outline-2">Start a new conversation</Link></div>;
}

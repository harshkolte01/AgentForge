import Link from "next/link";
import { BrandMark } from "@/components/brand-mark";
import { sections } from "@/features/landing/content";
import { routes } from "@/lib/routes";

export function SiteFooter() {
  return <footer>
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-8 sm:flex-row sm:items-center sm:justify-between sm:px-8">
      <Link href={routes.home} className="inline-flex min-h-11 items-center gap-2.5 self-start rounded text-sm font-medium"><BrandMark className="size-6" />AgentForge</Link>
      <nav aria-label="Footer" className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {sections.map(({ id, label }) => <a key={id} href={`#${id}`} className="lp-text-link inline-flex min-h-11 items-center rounded text-sm text-muted-foreground">{label}</a>)}
      </nav>
      <p className="text-xs text-muted-foreground">© {new Date().getFullYear()} AgentForge</p>
    </div>
  </footer>;
}

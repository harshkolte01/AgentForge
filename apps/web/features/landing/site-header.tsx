"use client";
import Link from "next/link";
import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { Menu, X } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { sections, type SectionId } from "@/features/landing/content";
import { HeaderActions } from "@/features/landing/landing-auth";
import { routes } from "@/lib/routes";

function subscribeScroll(listener: () => void) {
  window.addEventListener("scroll", listener, { passive: true });
  return () => window.removeEventListener("scroll", listener);
}
const scrolledNow = () => window.scrollY > 8;

/** The section nearest the top third of the screen, for highlighting its link. */
function useActiveSection(): SectionId | null {
  const [active, setActive] = useState<SectionId | null>(null);
  useEffect(() => {
    const nodes = sections.map(({ id }) => document.getElementById(id)).filter((node): node is HTMLElement => !!node);
    if (!nodes.length || typeof IntersectionObserver === "undefined") return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) { if (entry.isIntersecting) visible.add(entry.target.id); else visible.delete(entry.target.id); }
      const current = sections.find(({ id }) => visible.has(id));
      setActive(current ? current.id : null);
    }, { rootMargin: "-30% 0px -60% 0px" });
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, []);
  return active;
}

const linkClass = "rounded-full px-3 py-1.5 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60";

export function SiteHeader() {
  const scrolled = useSyncExternalStore(subscribeScroll, scrolledNow, () => false);
  const active = useActiveSection();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const toggle = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); toggle.current?.focus(); } };
    const wide = window.matchMedia("(min-width: 768px)");
    const onWide = () => { if (wide.matches) setOpen(false); };
    window.addEventListener("keydown", onKey);
    wide.addEventListener("change", onWide);
    return () => { window.removeEventListener("keydown", onKey); wide.removeEventListener("change", onWide); };
  }, [open]);

  return <header className={`lp-header sticky top-0 z-40 w-full transition-colors duration-300 ${scrolled || open ? "bg-background/80 backdrop-blur-xl supports-[backdrop-filter]:bg-background/70" : "bg-background"}`}>
    <div className="mx-auto flex h-[72px] w-full max-w-6xl items-center justify-between gap-4 px-5 sm:px-8">
      <Link href={routes.home} aria-label="AgentForge, home" className="-ml-1 flex items-center gap-2.5 rounded-lg px-1 py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <BrandMark className="size-8" /><span className="sr-only">AgentForge</span>
      </Link>
      <nav aria-label="Page sections" className="hidden items-center gap-0.5 md:flex">
        {sections.map(({ id, label }) => <a key={id} href={`#${id}`} aria-current={active === id ? "location" : undefined}
          className={`${linkClass} ${active === id ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{label}</a>)}
      </nav>
      <div className="flex items-center gap-2">
        <HeaderActions className="hidden md:flex" />
        <button ref={toggle} type="button" aria-expanded={open} aria-controls={menuId} aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen((value) => !value)}
          className="flex size-10 items-center justify-center rounded-full outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 md:hidden">
          {open ? <X className="size-5" aria-hidden="true" /> : <Menu className="size-5" aria-hidden="true" />}
        </button>
      </div>
    </div>
    {/* Over the page, not in the flow: closing it must not shift the content (and the scroll target) beneath. */}
    {open ? <div id={menuId} className="absolute inset-x-0 top-full bg-background shadow-lg md:hidden">
      <nav aria-label="Page sections" className="mx-auto flex max-w-6xl flex-col gap-1 px-5 pb-5 pt-3 sm:px-8">
        {sections.map(({ id, label }) => <a key={id} href={`#${id}`} onClick={() => setOpen(false)} aria-current={active === id ? "location" : undefined}
          className={`${linkClass} flex min-h-11 items-center text-base ${active === id ? "bg-muted text-foreground" : "text-muted-foreground"}`}>{label}</a>)}
        <div className="mt-3 flex items-center justify-between gap-3 border-t pt-4">
            <HeaderActions className="ml-auto" onNavigate={() => setOpen(false)} />
        </div>
      </nav>
    </div> : null}
  </header>;
}

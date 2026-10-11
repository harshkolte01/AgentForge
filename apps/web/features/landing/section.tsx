import type { ReactNode } from "react";
import { Reveal } from "@/features/landing/reveal";
import type { SectionId } from "@/features/landing/content";

/** One landing section. `id` is typed to the nav list, so a link can never point at a section that does not exist. */
export function Section({ id, title, description, children, className = "" }: {
  id: SectionId; title: ReactNode; description?: string; children: ReactNode; className?: string;
}) {
  return <section id={id} aria-labelledby={`${id}-title`} className={`scroll-mt-24 py-20 sm:py-28 ${className}`}>
    <div className="mx-auto w-full max-w-6xl px-5 sm:px-8">
      <Reveal className="mb-12 max-w-2xl sm:mb-16">
        <h2 id={`${id}-title`} className="text-balance text-3xl font-medium tracking-tight sm:text-4xl">{title}</h2>
        {description ? <p className="mt-4 text-pretty text-base leading-7 text-muted-foreground sm:text-lg sm:leading-8">{description}</p> : null}
      </Reveal>
      {children}
    </div>
  </section>;
}

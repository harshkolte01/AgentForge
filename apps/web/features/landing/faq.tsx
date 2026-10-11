import { Plus } from "lucide-react";
import { faqs } from "@/features/landing/content";
import { Reveal } from "@/features/landing/reveal";
import { Section } from "@/features/landing/section";

/** Native disclosure elements: keyboard and screen-reader behaviour for free, and it works with scripts off. `name` makes it exclusive. */
export function Faq() {
  return <Section id="faq" title="A few things to know.">
    <Reveal className="max-w-3xl space-y-2">
      {faqs.map((item) => <details key={item.q} name="faq" className="group rounded-lg open:bg-muted/30">
        <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 rounded-lg px-3 py-5 transition-colors hover:bg-muted/40 text-left text-base font-medium outline-none marker:hidden focus-visible:ring-2 focus-visible:ring-ring/60 [&::-webkit-details-marker]:hidden">
          {item.q}
          <Plus className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-45 motion-reduce:transition-none" aria-hidden="true" />
        </summary>
        <p className="px-3 pb-5 pr-10 text-[15px] leading-7 text-muted-foreground">{item.a}</p>
      </details>)}
    </Reveal>
  </Section>;
}

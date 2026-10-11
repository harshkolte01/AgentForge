import Image from "next/image";
import { PrimaryCta } from "@/features/landing/landing-auth";

export function FinalCta() {
  return <section aria-labelledby="cta-title" className="relative isolate overflow-hidden px-5 py-24 sm:px-8 sm:py-32">
    <Image src="/images/ascii-flow.webp" alt="" fill sizes="100vw" className="pointer-events-none -z-20 object-cover object-center opacity-40" />
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[linear-gradient(180deg,#0b0b0b_0%,transparent_35%,transparent_65%,#0b0b0b_100%)]" />
    <div className="mx-auto flex max-w-2xl flex-col items-center text-center">
      <h2 id="cta-title" className="text-balance text-4xl font-medium leading-[1.15] tracking-[-0.03em] sm:text-5xl">Your next task<br />starts here.</h2>
      <p className="mt-5 max-w-sm text-base leading-7 text-muted-foreground">An idea to explore. A problem to solve.<br />Bring it to AgentForge.</p>
      <PrimaryCta label="Get started" className="mt-8 w-full justify-between sm:w-[240px]" />
    </div>
  </section>;
}

import Image from "next/image";
import { HeroDemo } from "@/features/landing/hero-demo";
import { PrimaryCta } from "@/features/landing/landing-auth";
import { Wordmark } from "@/features/landing/wordmark";

export function Hero() {
  return <section aria-labelledby="hero-title" className="relative isolate overflow-hidden px-4 pb-16 sm:px-8 sm:pb-24">
    <Image src="/images/ascii-flow.webp" alt="" fill preload sizes="100vw" className="pointer-events-none -z-10 object-cover object-top opacity-20" />
    <div className="lp-backdrop-fade pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />
    <div className="mx-auto max-w-2xl pb-12 pt-14 sm:pb-16 sm:pt-20">
      <Wordmark className="mb-7 h-auto w-[200px] text-foreground sm:w-[240px]" />
      <h1 id="hero-title" className="text-balance text-4xl font-medium leading-[1.12] tracking-[-0.03em] sm:text-5xl">Build with a coding agent.<br />Keep the final say.</h1>
      <p className="mt-6 max-w-md text-base leading-7 text-muted-foreground">Research, write code, and review changes.<br className="hidden sm:block" /> All in one conversation.</p>
      <div className="mt-8"><PrimaryCta label="Get started" className="w-full justify-between sm:w-[240px]" /></div>
    </div>
    <div id="workflow-preview" className="mx-auto max-w-6xl scroll-mt-24">
      <HeroDemo />
      <p className="mt-5 text-center text-xs leading-6 text-muted-foreground">Try the demo. Approve or decline a proposed change.</p>
    </div>
  </section>;
}

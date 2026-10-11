import type { Metadata } from "next";
import "@/features/landing/landing.css";
import { Faq } from "@/features/landing/faq";
import { FinalCta } from "@/features/landing/final-cta";
import { Hero } from "@/features/landing/hero";
import { HowItWorks } from "@/features/landing/how-it-works";
import { Possibilities } from "@/features/landing/possibilities";
import { manrope } from "@/features/landing/fonts";
import { LandingAuth } from "@/features/landing/landing-auth";
import { SiteFooter } from "@/features/landing/site-footer";
import { SiteHeader } from "@/features/landing/site-header";

export const metadata: Metadata = {
  title: { absolute: "AgentForge: hand off the work, keep the final say" },
  description: "Research, write code, and review changes in one conversation with AgentForge. Bring an idea or connect a repository to get started.",
  alternates: { canonical: "/" },
};

/** The public landing page. Static: the signed-in app lives under `/chat` and `/settings`. */
export default function Home() {
  return <LandingAuth>
    <div data-landing data-public className={`${manrope.variable} dark relative isolate min-h-svh overflow-x-clip bg-background text-foreground`}>
      <a href="#main" className="sr-only z-50 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground focus:not-sr-only focus:fixed focus:left-4 focus:top-4">Skip to content</a>
      <SiteHeader />
      <main id="main">
        <Hero />
        <HowItWorks />
        <Possibilities />
        <Faq />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  </LandingAuth>;
}

import { steps } from "@/features/landing/content";
import { Section } from "@/features/landing/section";

export function HowItWorks() {
  return <Section id="how-it-works" title="From an idea to work you can review.">
    <ol className="grid gap-10 md:grid-cols-3 md:gap-12">
      {steps.map((step, index) => <li key={step.title}>
        <span className="text-sm tabular-nums text-muted-foreground" aria-hidden="true">0{index + 1}</span>
        <h3 className="mt-5 text-lg font-medium tracking-tight"><span className="sr-only">Step {index + 1}: </span>{step.title}</h3>
        <p className="mt-3 max-w-sm text-[15px] leading-7 text-muted-foreground">{step.body}</p>
      </li>)}
    </ol>
  </Section>;
}

import { examples } from "@/features/landing/content";

export function Possibilities() {
  return <section id="possibilities" aria-labelledby="possibilities-title" className="scroll-mt-24 py-20 sm:py-28">
    <div className="mx-auto grid max-w-6xl gap-12 px-5 sm:px-8 lg:grid-cols-[0.9fr_1.1fr] lg:gap-20">
      <div>
        <h2 id="possibilities-title" className="max-w-sm text-balance text-3xl font-medium tracking-tight sm:text-4xl">A fresh idea.<br />An existing codebase.<br /><span className="text-muted-foreground">Start where you are.</span></h2>
        <p className="mt-6 max-w-sm text-[15px] leading-7 text-muted-foreground">Bring the work you want to move forward. A good conversation starts with a simple request.</p>
      </div>
      <ul className="space-y-9">
        {examples.map((example) => <li key={example.title} className="max-w-xl">
          <h3 className="text-sm font-medium text-muted-foreground">{example.title}</h3>
          <p className="mt-3 text-pretty text-lg leading-8 tracking-tight sm:text-xl">“{example.prompt}”</p>
        </li>)}
      </ul>
    </div>
  </section>;
}

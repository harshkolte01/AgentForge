/** The one AgentForge mark (also `app/icon.svg`). Size and type size come from `className`. */
export function BrandMark({ className = "size-6 text-[11px]" }: { className?: string }) {
  return <span aria-hidden="true" className={`flex shrink-0 select-none items-center justify-center rounded-[28%] bg-primary font-semibold text-primary-foreground ${className}`}>A</span>;
}

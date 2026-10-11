import Image from "next/image";

/** Shared generated mark for navigation, authentication, loading, and landing-page previews. Shown at 32px at most: 64 covers 2x screens. */
export function BrandMark({ className = "size-6" }: { className?: string }) {
  return <Image src="/brand/agentforge-icon-64.png" width={64} height={64} alt="" aria-hidden="true"
    unoptimized draggable={false} className={`block shrink-0 select-none object-contain ${className}`} />;
}

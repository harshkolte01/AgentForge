import Image from "next/image";

/** Shared generated mark for navigation, authentication, loading, and landing-page previews. */
export function BrandMark({ className = "size-6" }: { className?: string }) {
  return <Image src="/brand/agentforge-icon-128.png" width={128} height={128} alt="" aria-hidden="true"
    unoptimized draggable={false} className={`block shrink-0 select-none object-contain ${className}`} />;
}

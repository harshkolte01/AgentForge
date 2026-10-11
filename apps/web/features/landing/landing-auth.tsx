"use client";
import Link from "next/link";
import { createContext, useContext, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { routes } from "@/lib/routes";
import { useAuthSession } from "@/lib/use-auth-session";

const SignedInContext = createContext(false);

/**
 * One session read for the whole landing page. The page is static and renders for a visitor who is not signed in; once the read
 * says otherwise, every call to action switches to the workspace. If the API cannot be reached the page stays as it was.
 */
export function LandingAuth({ children }: { children: ReactNode }) {
  const { state } = useAuthSession();
  return <SignedInContext.Provider value={state.status === "ready" && state.session.authenticated}>{children}</SignedInContext.Provider>;
}

const sizes = { md: "min-h-11 px-4 text-sm", lg: "min-h-11 px-4 text-[15px]" } as const;
const focus = "focus-visible:ring-offset-2 focus-visible:ring-offset-background";

/** The main call to action: create an account, or open the workspace when already signed in. */
export function PrimaryCta({ size = "lg", className = "", label = "Get started" }: { size?: keyof typeof sizes; className?: string; label?: string }) {
  const signedIn = useContext(SignedInContext);
  return <Link href={signedIn ? routes.newConversation : routes.signUp} className={`${buttonVariants()} ${sizes[size]} ${focus} gap-2 rounded-md font-medium shadow-sm ${className}`}>
    {signedIn ? "Open workspace" : label}<ArrowRight className="size-4" aria-hidden="true" />
  </Link>;
}

/** Header actions: Sign in + Get started, or just the workspace link when signed in. */
export function HeaderActions({ className = "", onNavigate }: { className?: string; onNavigate?: () => void }) {
  const signedIn = useContext(SignedInContext);
  return <div className={`flex items-center gap-2 ${className}`}>
    {signedIn ? null : <Link href={routes.signIn} onClick={onNavigate} className={`${buttonVariants({ variant: "ghost" })} ${sizes.md} rounded-md text-muted-foreground hover:text-foreground`}>Sign in</Link>}
    <Link href={signedIn ? routes.newConversation : routes.signUp} onClick={onNavigate} className={`${buttonVariants()} ${sizes.md} ${focus} rounded-md font-medium`}>{signedIn ? "Open workspace" : "Get started"}</Link>
  </div>;
}

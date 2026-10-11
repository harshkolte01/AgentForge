import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@/features/landing/landing.css";
import { AuthShell } from "@/features/auth/auth-shell";

// Nothing here is worth finding in a search: the landing page is the front door.
export const metadata: Metadata = { robots: { index: false, follow: true } };

export default function AuthLayout({ children }: { children: ReactNode }) {
  return <AuthShell>{children}</AuthShell>;
}

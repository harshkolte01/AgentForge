import type { Metadata } from "next";
import { AuthPanel } from "@/features/auth/auth-panel";
import { authQuery } from "@/features/auth/auth-query";

export const metadata: Metadata = { title: "Sign in", description: "Sign in to AgentForge with GitHub." };

export default async function SignInPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  return <AuthPanel mode="sign-in" query={authQuery(await searchParams)} />;
}

import type { Metadata } from "next";
import { AuthPanel } from "@/features/auth/auth-panel";
import { authQuery } from "@/features/auth/auth-query";

export const metadata: Metadata = { title: "Create your account", description: "Create your AgentForge account with GitHub." };

export default async function SignUpPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  return <AuthPanel mode="sign-up" query={authQuery(await searchParams)} />;
}

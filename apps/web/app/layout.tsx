import type { Metadata, Viewport } from "next";
import { themeScript } from "@/lib/preferences";
import "./globals.css";
export const metadata: Metadata = {
  title: "AgentForge",
  description: "Your workspace for coding, research, and problem solving.",
};
export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#ffffff" }, { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // The theme class is set by the inline script before the first paint (no light flash on a dark page); React never renders it,
  // hence `suppressHydrationWarning` on <html> only.
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}

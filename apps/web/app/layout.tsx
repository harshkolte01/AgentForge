import type { Metadata, Viewport } from "next";
import { themeScript } from "@/lib/preferences";
import { siteUrl } from "@/lib/site";
import "./globals.css";

const description = "Research the web, write and run code in a private cloud sandbox, and review changes with AgentForge. Choose Ask me mode to approve actions before they run.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: { default: "AgentForge", template: "%s · AgentForge" },
  description,
  applicationName: "AgentForge",
  openGraph: { type: "website", siteName: "AgentForge", title: "AgentForge: hand off the work, keep the final say", description },
  twitter: { card: "summary_large_image", title: "AgentForge: hand off the work, keep the final say", description },
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

import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "AgentForge AI",
  description: "Your workspace for coding, research, and problem solving.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}

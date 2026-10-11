import { ImageResponse } from "next/og";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const alt = "AgentForge: hand off the work, keep the final say";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

/** The social card for every page: the mark, the promise, and an approval, on the dark palette. */
export default async function OpenGraphImage() {
  const mark = await readFile(join(process.cwd(), "public/brand/agentforge-icon-128.png"));
  const markUrl = `data:image/png;base64,${mark.toString("base64")}`;
  return new ImageResponse(
    (
      <div style={{ display: "flex", width: "100%", height: "100%", flexDirection: "column", justifyContent: "space-between", padding: 72, color: "#fafafa",
        background: "#141312" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          {/* next/og renders this image directly; the browser Image component does not apply here. */}
          <img src={markUrl} width={56} height={56} alt="" />
          <div style={{ display: "flex", fontSize: 34, fontWeight: 600 }}>AgentForge</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 92, fontWeight: 700, lineHeight: 1.05, letterSpacing: -2 }}>
          <div style={{ display: "flex" }}>Hand off the work.</div>
          <div style={{ display: "flex", color: "#f1f0ee" }}>Keep the final say.</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", fontSize: 25, color: "#aaa7a3", maxWidth: 410 }}>Research, code and pull requests. Your approval in Ask me mode.</div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 22px", borderRadius: 18, border: "2px solid #3a3a3a", background: "#171717", fontSize: 21 }}>
            <div style={{ display: "flex", color: "#a3a3a3" }}>Open this pull request?</div>
            <div style={{ display: "flex", padding: "8px 18px", borderRadius: 12, background: "#fafafa", color: "#0a0a0a", fontWeight: 600 }}>Approve</div>
          </div>
        </div>
      </div>
    ),
    size,
  );
}

import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AgentForge",
    short_name: "AgentForge",
    description: "Research, code, and review work with your AI agents.",
    start_url: "/chat",
    display: "standalone",
    background_color: "#101112",
    theme_color: "#101112",
    icons: [192, 512].map((size) => ({
      src: `/brand/agentforge-icon-${size}.png`,
      sizes: `${size}x${size}`,
      type: "image/png",
      purpose: "any",
    })),
  };
}

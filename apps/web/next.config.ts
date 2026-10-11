import type { NextConfig } from "next";

const apiUrl = new URL(process.env.API_ORIGIN ?? "http://localhost:8000");
if (!["http:", "https:"].includes(apiUrl.protocol) || apiUrl.username || apiUrl.password || apiUrl.pathname !== "/" || apiUrl.search || apiUrl.hash) {
  throw new Error("API_ORIGIN must be an HTTP(S) origin without credentials, path, query or fragment.");
}
const nextConfig: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl.origin}/api/:path*`,
      },
    ];
  },
  // Old URLs keep working. The request's query is passed through (`?agent=`, `?create=1`, `?auth_error=`).
  async redirects() {
    return [
      { source: "/workspace", has: [{ type: "query", key: "session", value: "(?<session>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})" }], destination: "/chat/:session", permanent: false },
      { source: "/workspace", destination: "/chat", permanent: false },
      { source: "/connections", destination: "/settings?section=github", permanent: false },
      { source: "/integrations", destination: "/settings?section=github", permanent: false },
      { source: "/agents/new", destination: "/settings?section=agents&create=1", permanent: false },
      { source: "/agents", destination: "/settings?section=agents", permanent: false },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

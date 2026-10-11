import type { NextConfig } from "next";

const apiUrl = new URL(process.env.API_ORIGIN ?? "http://localhost:8000");
if (!["http:", "https:"].includes(apiUrl.protocol) || apiUrl.username || apiUrl.password || apiUrl.pathname !== "/" || apiUrl.search || apiUrl.hash) {
  throw new Error("API_ORIGIN must be an HTTP(S) origin without credentials, path, query or fragment.");
}
// Files in `public/` keep their names when they change, so they are cached for a day and then revalidated in the background,
// never marked immutable. (Hashed build output under `/_next/static` is already cached for good by Next.)
const publicCache = [{ key: "Cache-Control", value: "public, max-age=86400, stale-while-revalidate=604800" }];
const nextConfig: NextConfig = {
  devIndicators: false,
  poweredByHeader: false,
  // A self-contained server (`.next/standalone/server.js`) with only the files it needs, for the container image.
  output: "standalone",
  async headers() {
    return [
      { source: "/fonts/:path*", headers: publicCache },
      { source: "/brand/:path*", headers: publicCache },
      { source: "/images/:path*", headers: publicCache },
      // Only the logo files: `/plugins` itself is a signed-in page.
      { source: "/plugins/:logo([^/]+\\.(?:svg|png))", headers: publicCache },
    ];
  },
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
  // Keep Tailwind output identical when building with the Webpack fallback.
  webpack(config) {
    const addTailwind = (rules: { use?: unknown; oneOf?: unknown; rules?: unknown }[]) => {
      for (const rule of rules) {
        if (!rule || typeof rule !== "object") continue;
        if (Array.isArray(rule.oneOf)) addTailwind(rule.oneOf);
        if (Array.isArray(rule.rules)) addTailwind(rule.rules);
        if (Array.isArray(rule.use) && rule.use.some((entry) => {
          const loader = typeof entry === "string" ? entry : entry?.loader;
          return typeof loader === "string" && loader.includes("/css-loader/");
        })) rule.use.push({ loader: "@tailwindcss/turbopack" });
      }
    };
    addTailwind(config.module.rules);
    return config;
  },
};

export default nextConfig;

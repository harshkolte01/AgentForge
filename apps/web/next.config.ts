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

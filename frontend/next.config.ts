import type { NextConfig } from "next";

// Production is a static export served by Caddy next to the API (same origin, so the visitor cookie just works).
// In `next dev` the API runs separately on :8000 and /api + /mcp are proxied to it.
const isProd = process.env.NODE_ENV === "production";
const api = process.env.FB_API_ORIGIN || "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  images: { unoptimized: true },
  ...(isProd
    ? { output: "export" as const }
    : {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${api}/api/:path*` },
            { source: "/mcp", destination: `${api}/mcp` },
          ];
        },
      }),
};

export default config;

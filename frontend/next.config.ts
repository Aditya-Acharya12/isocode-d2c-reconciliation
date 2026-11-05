import type { NextConfig } from "next";

const apiOrigin = process.env.API_ORIGIN ?? "http://localhost:8000";

if (process.env.VERCEL && !process.env.API_ORIGIN) {
  // Rewrites are fixed at build time, so a missing value must fail the deploy.
  throw new Error("API_ORIGIN must be set in the Vercel project settings.");
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  experimental: {
    // `next start` buffers every proxied request body up to this limit, and larger
    // uploads then fail. It must stay above the API's MAX_UPLOAD_MB (default 25).
    proxyClientMaxBodySize: "30mb",
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // The browser only ever talks to this origin, so the session cookie is first-party.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;

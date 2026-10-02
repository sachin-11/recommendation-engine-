// API_PROXY_URL (e.g. the API's Railway URL) makes this server forward /api/* and /health to
// the API. The browser then calls the API on the dashboard's own origin: no CORS, and the
// API's address is not compiled into the bundle. Without it, NEXT_PUBLIC_API_BASE_URL says
// where the browser should call (local development, or a reverse proxy in front of both).
const apiProxyUrl = process.env.API_PROXY_URL?.replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Self-contained server bundle for the Docker image (dashboard/Dockerfile).
  output: "standalone",
  // An empty base URL means "same origin" (see lib/utils.ts).
  ...(apiProxyUrl && { env: { NEXT_PUBLIC_API_BASE_URL: "" } }),
  async rewrites() {
    if (!apiProxyUrl) return [];
    return [
      { source: "/api/:path*", destination: `${apiProxyUrl}/api/:path*` },
      { source: "/health", destination: `${apiProxyUrl}/health` },
    ];
  },
};

export default nextConfig;

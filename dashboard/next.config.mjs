/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Self-contained server bundle for the Docker image (dashboard/Dockerfile).
  output: "standalone",
};

export default nextConfig;

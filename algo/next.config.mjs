/** @type {import('next').NextConfig} */
const lowMemoryBuild = process.env.LOW_MEMORY_BUILD === "1";

const nextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  ...(lowMemoryBuild
    ? {
        experimental: {
          cpus: 1,
          workerThreads: false,
        },
      }
    : {}),
};

export default nextConfig;

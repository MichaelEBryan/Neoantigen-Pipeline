/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  rewrites: async () => ({
    beforeFiles: [
      {
        source: "/api/py/:path*",
        destination: `${process.env.BACKEND_INTERNAL_URL || 'http://localhost:8000'}/:path*`,
      },
    ],
  }),
};

export default nextConfig;

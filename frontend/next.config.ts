import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  distDir: process.env.DEALGUARD_NEXT_DIST_DIR ?? ".next",
};

export default nextConfig;

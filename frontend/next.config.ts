import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // 关闭 Next.js 开发模式右下角/右上角的 Turbopack 指示器黑圈
  devIndicators: false,
};

export default nextConfig;

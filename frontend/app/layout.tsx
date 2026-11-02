import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DealGuard",
  description: "Policy-aware trusted business transaction workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

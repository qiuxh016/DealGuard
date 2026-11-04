import type { Metadata } from "next";
import "./globals.css";
import "./design-system.css";
import { LanguageProvider } from "../components/LanguageProvider";
import { GettingStartedWidget } from "../components/GettingStartedWidget";

export const metadata: Metadata = {
  title: "DealGuard",
  description: "Policy-aware trusted business transaction workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="dg-design-system"><LanguageProvider>{children}<GettingStartedWidget /></LanguageProvider></body>
    </html>
  );
}

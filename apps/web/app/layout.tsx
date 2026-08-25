import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "DocAlign · 文档格式合规工作台",
  description: "确定性 DOCX 格式化、内容保护与合规验证。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}

import type { ReactNode } from "react";
import "./style.css";

export const metadata = { title: "ResolveAI", description: "Synthetic after-sales demo" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}

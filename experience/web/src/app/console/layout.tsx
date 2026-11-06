import type { Metadata } from "next";
import { Shell } from "@/components/console/Shell";

export const metadata: Metadata = { title: "Staff console", robots: { index: false } };

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <Shell>{children}</Shell>;
}

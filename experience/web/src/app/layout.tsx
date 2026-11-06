import type { Metadata, Viewport } from "next";
import { Inter, Montserrat, Noto_Sans } from "next/font/google";
import "./globals.css";

const montserrat = Montserrat({ subsets: ["latin"], variable: "--font-montserrat", display: "swap" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const noto = Noto_Sans({ subsets: ["latin"], variable: "--font-noto", display: "swap" });

export const metadata: Metadata = {
  title: { default: "AdmitAI", template: "%s · AdmitAI" },
  description: "Admissions assistant, human handoff and staff console (Module C)",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${montserrat.variable} ${inter.variable} ${noto.variable}`}>
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ChartEdge AI",
  description: "NSE technical analysis paper trading dashboard"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

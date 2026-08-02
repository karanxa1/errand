import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Errand — the agent that shops and pays, with your say-so",
  description:
    "A voice-first agent that shops an approved merchant and pays via Prava — a human approves every spend.",
};

export const viewport: Viewport = {
  themeColor: "#070b09",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

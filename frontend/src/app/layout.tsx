import type { Metadata } from "next";
import "./globals.css";
import Providers from "@/components/providers";

// Fonts: Inter + JetBrains Mono loaded via CSS fallback stack.
// In production, self-host woff2 files or use next/font/google when network is available.

export const metadata: Metadata = {
  title: "Oxford Cancer Vaccine Design",
  description:
    "AI-powered cancer vaccine design and epitope prediction platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased min-h-screen bg-background text-foreground">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

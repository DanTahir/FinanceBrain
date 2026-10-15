import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FinanceBrain — a FalkorDB company brain",
  description:
    "Multi-agent company brain for a synthetic finance firm: PII redacted before ingestion, every answer cited, " +
    "investigations with human approval, per-user memory. Built on FalkorDB.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">{children}</body>
    </html>
  );
}

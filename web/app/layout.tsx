import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import { Shell } from "@/components/Shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "PRI — Production Resilience Intelligence",
  description:
    "A stateful digital twin for film production. Deterministic software computes every number; Gemini interprets and explains.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* The shell reads `?production=`, and a client component that reads
            search params has to sit under a Suspense boundary or every page
            below it opts out of static rendering. */}
        <Suspense>
          <Shell>{children}</Shell>
        </Suspense>
      </body>
    </html>
  );
}

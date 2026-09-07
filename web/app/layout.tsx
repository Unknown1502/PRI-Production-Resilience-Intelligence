import type { Metadata } from "next";
import { Archivo, Archivo_Narrow, Courier_Prime } from "next/font/google";
import { Suspense, type ReactNode } from "react";

import { Shell } from "@/components/Shell";

import "./globals.css";

/**
 * Two families, doing two different jobs.
 *
 * Archivo is the interface: a grotesque with enough width and weight range to
 * carry a dense operational screen, and a narrow cut for the strips, where
 * horizontal space is the constraint a real board has too.
 *
 * Courier Prime is for scene slugs and script content only. Screenplays are
 * set in Courier by convention old enough to be law, so a slug printed in it
 * looks like what it is — a line lifted off a script page — rather than like a
 * monospaced data label.
 */
const archivo = Archivo({
  subsets: ["latin"],
  variable: "--font-archivo",
  display: "swap",
});

const archivoNarrow = Archivo_Narrow({
  subsets: ["latin"],
  variable: "--font-archivo-condensed",
  display: "swap",
});

const courierPrime = Courier_Prime({
  subsets: ["latin"],
  weight: ["400", "700"],
  variable: "--font-courier-prime",
  display: "swap",
});

/**
 * Set the theme before the first paint.
 *
 * React cannot do this: the server has no idea what the viewer chose, so any
 * server-rendered guess is wrong half the time and corrects itself after
 * hydration — which the viewer sees as the page flashing the other theme. This
 * runs synchronously in <head>, before the body is painted.
 *
 * It must agree with `ThemeToggle`: same storage key, same resolution rule.
 */
const NO_FLASH = `
(function () {
  try {
    var stored = localStorage.getItem('pri-theme');
    var dark = stored === 'board' ||
      (stored !== 'callsheet' &&
       window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
`;

export const metadata: Metadata = {
  title: "PRI — Production Resilience Intelligence",
  description:
    "A stateful digital twin for film production. Deterministic software computes every number; Gemini interprets and explains.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${archivo.variable} ${archivoNarrow.variable} ${courierPrime.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
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

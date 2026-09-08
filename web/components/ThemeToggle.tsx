"use client";

/**
 * Light / dark / follow-the-system, as three segments rather than a cycle.
 *
 * A cycling button hides two of its three states: you press it to find out
 * where it goes, and you cannot tell which one is on without reading the
 * label. Three segments show the whole choice and make the current one
 * obvious, which is worth the extra width in a top bar.
 *
 * "System" is a state in its own right, not the absence of a choice — someone
 * who has never touched this should follow their machine, including when their
 * machine changes at sunset.
 *
 * The applied theme is written to `data-theme` on <html>, which is what the CSS
 * variables key off. The choice is written to localStorage under the same key
 * the inline script in `layout.tsx` reads before first paint; if those two ever
 * disagree, the page flashes the wrong theme on every load.
 */

import { useCallback, useEffect, useState } from "react";

export const THEME_KEY = "pri-theme";

type Choice = "light" | "dark" | "system";

/** Resolve a choice to the attribute the stylesheet reads. */
function apply(choice: Choice): void {
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

function SunIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
      <circle cx="8" cy="8" r="3.1" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
        <path d="M8 1v1.8M8 13.2V15M15 8h-1.8M2.8 8H1M12.95 3.05l-1.27 1.27M4.32 11.68l-1.27 1.27M12.95 12.95l-1.27-1.27M4.32 4.32L3.05 3.05" />
      </g>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
      {/* A crescent cut from one disc by another, so it stays a crescent at
          any size rather than collapsing into a blob. */}
      <path
        d="M13.2 10.1A5.8 5.8 0 0 1 5.9 2.8a5.8 5.8 0 1 0 7.3 7.3z"
        fill="currentColor"
      />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden="true">
      <rect
        x="1.6"
        y="2.6"
        width="12.8"
        height="9"
        rx="1.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path
        d="M6 13.8h4"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

const OPTIONS: Array<{ value: Choice; label: string; icon: () => React.ReactElement }> = [
  { value: "light", label: "Call sheet", icon: SunIcon },
  { value: "dark", label: "Strip board", icon: MoonIcon },
  { value: "system", label: "Match system", icon: SystemIcon },
];

/**
 * Storage access that cannot take the page down with it.
 *
 * These four calls were bare. In a browser with site data blocked, *reading*
 * `window.localStorage` throws rather than returning null — and because this
 * component is mounted in the header of every route, the exception unmounted
 * the whole tree. The console rendered "Application error: a client-side
 * exception has occurred" and nothing else: not a lost theme preference, the
 * entire product. Found by the orientation band's own acceptance test, which
 * asks that the board still render with storage throwing.
 *
 * Losing the preference is the correct degradation. The inline script in
 * `app/layout.tsx` already guards its own read the same way, so the page still
 * paints in the system theme.
 */
function readTheme(): string | null {
  try {
    return window.localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

function writeTheme(next: Choice): void {
  try {
    if (next === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, next);
  } catch {
    // The choice applies to this page view and is simply not remembered.
  }
}

export function ThemeToggle() {
  // Null until the client has read storage. The server cannot know the choice,
  // so rendering a guess produces a hydration mismatch and a visible flicker.
  const [choice, setChoice] = useState<Choice | null>(null);

  useEffect(() => {
    const stored = readTheme();
    setChoice(stored === "light" || stored === "dark" ? stored : "system");

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if (readTheme() === null) apply("system");
    };
    media.addEventListener("change", onSystemChange);
    return () => media.removeEventListener("change", onSystemChange);
  }, []);

  const choose = useCallback((next: Choice) => {
    writeTheme(next);
    apply(next);
    setChoice(next);
  }, []);

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="flex items-center gap-0.5 rounded-md border border-board-600 bg-board-700 p-0.5"
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const active = choice === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => choose(value)}
            className={`flex h-6 w-7 items-center justify-center rounded transition-colors ${
              active
                ? "bg-board-500 text-chalk-100"
                : "text-chalk-400 hover:bg-board-600 hover:text-chalk-200"
            }`}
          >
            <Icon />
          </button>
        );
      })}
    </div>
  );
}

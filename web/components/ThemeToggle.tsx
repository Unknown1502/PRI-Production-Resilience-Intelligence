"use client";

/**
 * Switch between the board and the call sheet.
 *
 * Three states rather than two, because "follow the system" is a real answer
 * and forcing a choice at first load is not. A viewer who has never touched
 * the control gets whatever their machine already decided.
 *
 * The applied theme is written to `data-theme` on <html>, which is what the
 * CSS variables key off. The choice is written to localStorage under the same
 * key the inline script in `layout.tsx` reads before first paint — if those two
 * ever disagree, the page flashes the wrong theme on every load.
 */

import { useCallback, useEffect, useState } from "react";

export const THEME_KEY = "pri-theme";

type Choice = "board" | "callsheet" | "system";

const NEXT: Record<Choice, Choice> = {
  system: "board",
  board: "callsheet",
  callsheet: "system",
};

const LABEL: Record<Choice, string> = {
  system: "Match system",
  board: "Strip board",
  callsheet: "Call sheet",
};

/** Resolve a choice to the attribute the stylesheet reads. */
function apply(choice: Choice): void {
  const dark =
    choice === "board" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

export function ThemeToggle() {
  // Starts as null so the button renders nothing theme-dependent until the
  // client has read storage — the server cannot know the choice, and rendering
  // a guess produces a hydration mismatch.
  const [choice, setChoice] = useState<Choice | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_KEY);
    const initial: Choice =
      stored === "board" || stored === "callsheet" ? stored : "system";
    setChoice(initial);

    // Someone on "system" should follow the system when it changes.
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = () => {
      if (window.localStorage.getItem(THEME_KEY) === null) apply("system");
    };
    media.addEventListener("change", onSystemChange);
    return () => media.removeEventListener("change", onSystemChange);
  }, []);

  const cycle = useCallback(() => {
    setChoice((current) => {
      const next = NEXT[current ?? "system"];
      if (next === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, next);
      apply(next);
      return next;
    });
  }, []);

  return (
    <button
      type="button"
      onClick={cycle}
      className="btn-quiet"
      // The label says what is on now; the title says what pressing does.
      title={choice ? `Switch to ${LABEL[NEXT[choice]].toLowerCase()}` : "Change theme"}
      aria-label={choice ? `Theme: ${LABEL[choice]}. Change it.` : "Change theme"}
    >
      <span suppressHydrationWarning>{choice ? LABEL[choice] : "Theme"}</span>
    </button>
  );
}

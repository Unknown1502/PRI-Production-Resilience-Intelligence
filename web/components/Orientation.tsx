"use client";

/**
 * What this is, on the screen people actually open.
 *
 * The console opened straight onto a strip board: a film shooting schedule,
 * seven nouns in a sidebar, and a Reset button. Everything that explains the
 * claim lived in the README, which a first-time visitor reads after forming an
 * impression, if at all. The sentence below is the README's own — it is a good
 * sentence and it was doing no work in the product.
 *
 * The second line exists because `Overview / Import / Disruption / Recovery /
 * Governance / Verification / Audit` is an exact map of the pipeline to anyone
 * who already knows there is a pipeline, and seven disconnected nouns to
 * everyone else. Naming the stages in nav order turns the sidebar into a
 * table of contents.
 *
 * Deliberately not: a modal, a tour, a carousel, or an animation. It is a band
 * you can read in six seconds and dismiss forever, and it takes no signal
 * colour — `stamp` means refused and `seal` means verified everywhere else in
 * this interface, and spending either on an explainer devalues them on the
 * screens where they carry the argument.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

export const ORIENTATION_KEY = "pri-orientation-dismissed";

export function Orientation() {
  // `null` is "not yet known". Rendering either way before localStorage has
  // been read makes the band flash in or out on every load, which is worse
  // than either state.
  const [dismissed, setDismissed] = useState<boolean | null>(null);

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(ORIENTATION_KEY) === "1");
    } catch {
      // Site data blocked. Show it — the cost of seeing an explainer twice is
      // lower than the cost of never seeing it, and a storage exception must
      // never take the board down with it.
      setDismissed(false);
    }
  }, []);

  useEffect(() => {
    // Re-opened from the header, which clears the key and dispatches this.
    const reopen = () => setDismissed(false);
    window.addEventListener("pri:orientation-open", reopen);
    return () => window.removeEventListener("pri:orientation-open", reopen);
  }, []);

  if (dismissed !== false) return null;

  const dismiss = () => {
    setDismissed(true);
    try {
      localStorage.setItem(ORIENTATION_KEY, "1");
    } catch {
      // Not being able to remember the dismissal is not a reason to refuse it.
    }
  };

  return (
    <section
      aria-label="What PRI is"
      className="relative rounded-md border border-board-500 bg-board-700 px-5 py-4 pr-12"
    >
      <p className="max-w-3xl text-sm leading-relaxed text-chalk-200">
        A stateful digital twin for film production that computes what a disruption
        actually costs, proves its own recovery plans are legal, and refuses to execute
        one a human has not approved.
      </p>

      <p className="mt-2 max-w-3xl text-2xs leading-relaxed text-chalk-600">
        The screens follow the pipeline, in order: a disruption arrives → impact is
        computed → plans are generated and checked → a human approves → seven gates run
        → six checks confirm the write → the audit log records all of it.
      </p>

      <Link
        href="/disruption"
        className="mt-3 inline-block rounded-sm border border-board-500 bg-board-800 px-3 py-1.5
                   text-2xs font-medium text-chalk-200 hover:border-chalk-600"
      >
        See it recover from a disruption
      </Link>

      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss this introduction"
        className="absolute right-3 top-3 rounded-sm border border-board-500 px-2 py-0.5 text-2xs
                   text-chalk-600 hover:text-chalk-200 focus-visible:outline focus-visible:outline-2
                   focus-visible:outline-offset-2 focus-visible:outline-chalk-600"
      >
        Dismiss
      </button>
    </section>
  );
}

/** Clear the dismissal and tell any mounted band to come back. */
export function reopenOrientation() {
  try {
    localStorage.removeItem(ORIENTATION_KEY);
  } catch {
    // The event below still re-opens it for this page view.
  }
  window.dispatchEvent(new Event("pri:orientation-open"));
}

"use client";

/**
 * The button that starts the demo.
 *
 * Without it the only instruction on the disruption screen was "publish one
 * with scripts/emit_disruption.py", which a judge opening a hosted URL cannot
 * do — and it referred to a "Run demo" control that did not exist. The most
 * important screen in the project ended in a dead end.
 *
 * It posts the same disruption the fixture ships and then asks for a recovery,
 * in that order, because that is the order the pipeline expects: the event is
 * recorded first and is what the recovery is *for*. Both calls go through the
 * same proxy as everything else, so the viewer needs no credential.
 *
 * The SSE stream does the narrating. This only starts the clock.
 */

import { useCallback, useState } from "react";

import { api } from "@/lib/api";
import type { Schedule } from "@/lib/types";

import { useShell } from "@/components/Shell";

/**
 * A disruption that fits the production it is fired at.
 *
 * This used to return one hard-coded event: LOC-04, blocked across a night in
 * September 2026 — the location and dates the Night Train fixture turns on.
 * That was fine while the button could only ever act on the seeded demo. The
 * moment it started following the production on screen, pressing it on a
 * freshly imported film sent an event naming a location that film does not
 * have, on a date it does not shoot. Nothing errored: impact resolved to zero
 * scenes, so the planner correctly generated no candidates, and the recovery
 * screen sat empty while the pipeline reported success.
 *
 * So the event is built from the board instead. The busiest shooting day is
 * chosen because blocking it has the largest blast radius and therefore the
 * most to show; its own location and its own date come from the schedule.
 */
function disruptionFor(schedule: Schedule): Record<string, unknown> | null {
  const shootDays = schedule.days.filter(
    (day) => day.day_kind === "SHOOT" && day.scene_ids.length > 0 && day.location_id,
  );
  if (shootDays.length === 0) return null;

  const busiest = shootDays.reduce((a, b) => (b.scene_ids.length > a.scene_ids.length ? b : a));

  // The whole calendar day, in the board's own offset, so the window lines up
  // with the days the validator compares against.
  const offset = busiest.call_time.slice(-6);
  const stamp = /[+-]\d{2}:\d{2}$/.test(offset) ? offset : "+00:00";

  return {
    event_id: `demo-${Date.now()}`,
    event_type: "location.blocked",
    occurred_at: new Date().toISOString(),
    source: "location_manager",
    severity: 0.8,
    payload: {
      location_id: busiest.location_id,
      window_start: `${busiest.date}T00:00:00${stamp}`,
      window_end: `${nextDay(busiest.date)}T00:00:00${stamp}`,
      reason: `${busiest.location_name || busiest.location_id} is unavailable for the day`,
    },
  };
}

/** The calendar day after an ISO date, as an ISO date. */
function nextDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

export function RunDisruption({ productionId }: { productionId?: string } = {}) {
  // Defaults to whatever production the console is showing, which after an
  // import is the one just uploaded rather than the seeded demo. Pinning
  // this to the constant meant every screen but Overview quietly operated
  // on film-001 while the header named the imported film.
  const shell = useShell();
  const target = productionId ?? shell.productionId;
  const [stage, setStage] = useState<"idle" | "sending" | "recovering" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setError(null);
    setStage("sending");
    try {
      // Read the board first: the disruption has to name something this
      // production actually has.
      const schedule = await api.schedule(target);
      const event = disruptionFor(schedule);
      if (event === null) {
        setStage("failed");
        setError("This production has no shooting day to disrupt.");
        return;
      }
      await api.ingestEvent(target, event);
      setStage("recovering");
      // Awaited, so the button stays busy for as long as the work does. The
      // timeline fills from the stream while this is in flight.
      await api.recover(target, String(event.event_id));
      setStage("idle");
    } catch (cause) {
      setStage("failed");
      setError(cause instanceof Error ? cause.message : "Could not start the recovery.");
    }
  }, [target]);

  const busy = stage === "sending" || stage === "recovering";

  return (
    <div className="flex flex-col items-center gap-2">
      <button type="button" className="btn-go" onClick={run} disabled={busy}>
        {stage === "sending"
          ? "Reporting…"
          : stage === "recovering"
            ? "Finding options…"
            : "Report a disruption"}
      </button>
      {error ? (
        <p className="text-2xs text-stamp" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

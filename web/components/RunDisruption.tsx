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

import { PRODUCTION_ID, api } from "@/lib/api";

/**
 * The canonical demo disruption: the location the whole scenario turns on,
 * blocked for the day that forces the schedule to move.
 */
function demoEvent(): Record<string, unknown> {
  return {
    event_id: `demo-${Date.now()}`,
    event_type: "location.blocked",
    occurred_at: new Date().toISOString(),
    source: "location_manager",
    severity: 0.8,
    payload: {
      location_id: "LOC-04",
      window_start: "2026-09-10T00:00:00+05:30",
      window_end: "2026-09-11T00:00:00+05:30",
      reason: "Municipal permit withdrawn for a festival procession",
    },
  };
}

export function RunDisruption({ productionId = PRODUCTION_ID }: { productionId?: string }) {
  const [stage, setStage] = useState<"idle" | "sending" | "recovering" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setError(null);
    setStage("sending");
    const event = demoEvent();
    try {
      await api.ingestEvent(productionId, event);
      setStage("recovering");
      // Awaited, so the button stays busy for as long as the work does. The
      // timeline fills from the stream while this is in flight.
      await api.recover(productionId, String(event.event_id));
      setStage("idle");
    } catch (cause) {
      setStage("failed");
      setError(cause instanceof Error ? cause.message : "Could not start the recovery.");
    }
  }, [productionId]);

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

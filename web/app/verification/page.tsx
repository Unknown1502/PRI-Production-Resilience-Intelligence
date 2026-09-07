"use client";

/**
 * Verification — the six checks, the before/after diff, and the new call sheet.
 *
 * Validation asks whether a plan would be legal. This screen answers the other
 * question: is what we actually wrote what we said we would write. The checks
 * are recomputed by the API on every load rather than read back from a table,
 * because a stored verdict only proves something once wrote it there.
 */

import { useCallback, useEffect, useState } from "react";

import { PRODUCTION_ID, api, clock, shortDate } from "@/lib/api";
import type { Schedule, ScheduleDay, VerificationView } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { CheckRow, EmptyState, Panel, StatusPill } from "@/components/primitives";

export default function VerificationPage() {
  const { frames, version } = useShell();
  const [view, setView] = useState<VerificationView | null>(null);
  const [current, setCurrent] = useState<Schedule | null>(null);
  const [previous, setPrevious] = useState<Schedule | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (target: number) => {
    if (target < 2) {
      setView(null);
      setError(null);
      return;
    }
    try {
      const [verification, now, before] = await Promise.all([
        api.verification(PRODUCTION_ID, target),
        api.schedule(PRODUCTION_ID, target),
        api.schedule(PRODUCTION_ID, target - 1),
      ]);
      setView(verification);
      setCurrent(now);
      setPrevious(before);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load verification.");
    }
  }, []);

  useEffect(() => {
    if (version !== null) void load(version);
  }, [version, load]);

  useEffect(() => {
    const last = frames[frames.length - 1];
    if (last?.stage === "VERIFIED" && typeof last.new_version === "number") {
      void load(last.new_version);
    }
  }, [frames, load]);

  if (version !== null && version < 2) {
    return (
      <div className="p-6">
        <Panel title="Verification">
          <EmptyState>
            Nothing has been executed yet. Version 1 is the seeded baseline — the
            six checks describe a transition, and there has not been one.
          </EmptyState>
        </Panel>
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className="p-6">
        <Panel title="Verification">
          <EmptyState>{error}</EmptyState>
        </Panel>
      </div>
    );
  }

  if (view === null) {
    return (
      <div className="p-6">
        <Panel title="Verification">
          <EmptyState>Recomputing the checks…</EmptyState>
        </Panel>
      </div>
    );
  }

  const callSheets = view.artifacts.filter((a) => a.kind === "call_sheet");

  return (
    <div className="space-y-4 p-6">
      <Panel title="State version">
        <div className="flex flex-wrap items-center gap-6 px-4 py-4">
          <div className="flex items-baseline gap-3">
            <span className="tnum text-2xl text-chalk-600">v{view.parent_version ?? "—"}</span>
            <span className="text-chalk-600">→</span>
            <span className="tnum text-2xl font-bold text-clear">v{view.version}</span>
          </div>
          <div>
            <p className="field-label">digest</p>
            <p className="tnum text-xs text-chalk-200">{view.digest.slice(0, 24)}…</p>
          </div>
          <div className="ml-auto">
            <StatusPill tone={view.valid ? "clear" : "alert"} pulse={!view.valid}>
              {view.valid ? "verified" : "verification failed"}
            </StatusPill>
          </div>
        </div>
      </Panel>

      <Panel title="The six checks">
        <ul>
          {view.checks.map((check) => (
            <CheckRow
              key={check.code}
              code={check.code}
              name={check.name}
              passed={check.passed}
              detail={check.detail}
            />
          ))}
        </ul>
      </Panel>

      <Panel title="Schedule diff">
        {current === null || previous === null ? (
          <EmptyState>Comparison unavailable.</EmptyState>
        ) : (
          <ScheduleDiff before={previous} after={current} />
        )}
      </Panel>

      <Panel title="Artifacts">
        {callSheets.length === 0 ? (
          <EmptyState>No call sheet registered for this version.</EmptyState>
        ) : (
          <ul className="divide-y divide-ink-700">
            {callSheets.map((artifact) => (
              <li key={artifact.id} className="flex items-center gap-3 px-4 py-2.5">
                <span className="tnum text-xs text-chalk-100">
                  {artifact.path.split(/[\\/]/).pop()}
                </span>
                <span className="text-2xs text-chalk-600">v{artifact.version}</span>
                <a
                  className="btn-quiet ml-auto"
                  href={api.artifactUrl(artifact.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Download call sheet
                </a>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

/**
 * Before and after, with only the changed rows highlighted.
 *
 * A full diff of a 26-day shoot is noise. What a producer needs is the two or
 * three days that moved, and what they moved to.
 */
function ScheduleDiff({ before, after }: { before: Schedule; after: Schedule }) {
  const beforeByDate = new Map(before.days.map((day) => [day.date, day]));
  const rows = after.days.map((day) => ({
    day,
    was: beforeByDate.get(day.date) ?? null,
  }));
  const changed = rows.filter(({ day, was }) => was !== null && !sameDay(day, was));

  if (changed.length === 0) {
    return <EmptyState>No shooting day changed between these two versions.</EmptyState>;
  }

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-ink-700 text-left">
          <th className="field-label px-4 py-2">day</th>
          <th className="field-label py-2">was</th>
          <th className="field-label py-2">now</th>
        </tr>
      </thead>
      <tbody>
        {changed.map(({ day, was }) => (
          <tr key={day.date} className="border-b border-ink-700 bg-caution/5 last:border-b-0">
            <td className="px-4 py-2.5 align-top text-chalk-100">{shortDate(day.date)}</td>
            <td className="py-2.5 align-top text-chalk-600">
              {was ? <DayLine day={was} /> : "—"}
            </td>
            <td className="py-2.5 align-top text-chalk-100">
              <DayLine day={day} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DayLine({ day }: { day: ScheduleDay }) {
  return (
    <span className="tnum">
      {clock(day.call_time)}–{clock(day.wrap_time)} · {day.location_id} ·{" "}
      {day.scene_ids.length > 0 ? day.scene_ids.join(" ") : "no scenes"}
    </span>
  );
}

function sameDay(a: ScheduleDay, b: ScheduleDay): boolean {
  return (
    a.call_time === b.call_time &&
    a.wrap_time === b.wrap_time &&
    a.location_id === b.location_id &&
    a.scene_ids.join(",") === b.scene_ids.join(",")
  );
}

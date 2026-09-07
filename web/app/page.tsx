"use client";

/**
 * Production Overview — the schedule strip.
 *
 * This is the screen the video opens on, so it has to make the shape of the
 * production legible in about three seconds: eight days, where each one is,
 * how long it runs, and what it carries.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, clock, shortDate } from "@/lib/api";
import type { Schedule, ScheduleDay } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { EmptyState, MetricCell, Panel, StatusPill } from "@/components/primitives";

export default function OverviewPage() {
  const { productionId, frames, refreshVersion } = useShell();
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setSchedule(null);
    api
      .schedule(productionId)
      .then((next) => {
        setSchedule(next);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [productionId]);

  useEffect(load, [load]);
  useEffect(() => {
    if (frames[frames.length - 1]?.stage === "VERIFIED") load();
  }, [frames, load]);

  if (error !== null && schedule === null) {
    return (
      <div className="p-6">
        <Panel title="Overview">
          <EmptyState>
            <span className="space-y-3">
              <span className="block">
                There is no production called{" "}
                <span className="tnum text-chalk-400">{productionId}</span> to show ({error}).
                Either the API is still waking up, or nothing has been imported yet.
              </span>
              <Link href="/import" className="btn-go">
                Import a production
              </Link>
            </span>
          </EmptyState>
        </Panel>
      </div>
    );
  }

  if (schedule === null) {
    return (
      <div className="p-6">
        <Panel title="Overview">
          <EmptyState>Loading the production…</EmptyState>
        </Panel>
      </div>
    );
  }

  const working = schedule.days.filter((day) => day.scene_ids.length > 0);
  const totalMinutes = working.reduce((sum, day) => sum + day.scheduled_minutes, 0);

  return (
    <div className="space-y-4 p-6">
      <Panel title="Production">
        <div className="grid grid-cols-2 gap-6 px-4 py-4 sm:grid-cols-5">
          <MetricCell label="version" value={`v${schedule.version}`} />
          <MetricCell label="shoot days" value={working.length} hint={`${schedule.days.length} scheduled`} />
          <MetricCell
            label="scheduled"
            value={(totalMinutes / 60).toFixed(1)}
            unit="h"
            hint="scene time, setups excluded"
          />
          <MetricCell label="reserve days" value={schedule.reserve_days.length} />
          <MetricCell
            label="window"
            value={`${shortDate(schedule.shoot_start)} → ${shortDate(schedule.shoot_end)}`}
          />
        </div>
      </Panel>

      <Panel
        title="Schedule"
        actions={
          <button type="button" className="btn-quiet" onClick={() => { load(); refreshVersion(); }}>
            Refresh
          </button>
        }
      >
        <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4">
          {schedule.days.map((day) => (
            <DayCard key={day.date} day={day} />
          ))}
        </div>
      </Panel>
    </div>
  );
}

function DayCard({ day }: { day: ScheduleDay }) {
  const idle = day.scene_ids.length === 0;
  const hours = (
    (new Date(day.wrap_time).getTime() - new Date(day.call_time).getTime()) /
    3_600_000
  ).toFixed(1);

  return (
    <article
      className={`rounded-md border p-3 transition-colors ${
        idle ? "border-dashed border-ink-600 bg-ink-900" : "border-ink-600 bg-ink-800"
      }`}
    >
      <header className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold text-chalk-100">{shortDate(day.date)}</h3>
        {day.is_reserve ? <StatusPill tone="caution">reserve</StatusPill> : null}
      </header>

      <p className="mt-1.5 truncate text-2xs text-chalk-400" title={day.location_name}>
        {day.location_name}
      </p>

      <div className="mt-2.5 flex items-baseline gap-2">
        <span className="tnum text-base text-chalk-100">{clock(day.call_time)}</span>
        <span className="text-chalk-600">→</span>
        <span className="tnum text-base text-chalk-100">{clock(day.wrap_time)}</span>
        <span className="tnum ml-auto text-2xs text-chalk-600">{hours}h</span>
      </div>

      <div className="mt-2.5 border-t border-ink-700 pt-2">
        {idle ? (
          <p className="text-2xs text-chalk-600">No scenes scheduled</p>
        ) : (
          <p className="tnum text-xs text-chalk-200">{day.scene_ids.join("  ")}</p>
        )}
      </div>
    </article>
  );
}

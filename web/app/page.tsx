"use client";

/**
 * The board.
 *
 * This is the screen the video opens on, so it is the production itself rather
 * than a summary of it: one channel per shooting day, one strip per scene,
 * coloured by the standard a 1st AD already reads without thinking —
 *
 *     white   INT / DAY        blue    INT / NIGHT
 *     yellow  EXT / DAY        green   EXT / NIGHT
 *
 * Which means the shape of the shoot is visible before a word is read: a wall
 * of blue is a night block, a yellow run is exteriors and therefore weather
 * risk, and a gap in the strips is a day with nothing on it.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, clock, shortDate } from "@/lib/api";
import type { Schedule, ScheduleDay, SceneStrip } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { EmptyState, Panel } from "@/components/primitives";

/** The strip board's colour code, which predates all of this by decades. */
const STRIP_COLOUR: Record<string, string> = {
  "INT-DAY": "bg-strip-int-day",
  "EXT-DAY": "bg-strip-ext-day",
  "INT-NIGHT": "bg-strip-int-night",
  "EXT-NIGHT": "bg-strip-ext-night",
  // Dawn and dusk are shot as night work and boarded with the night colours.
  "INT-DAWN": "bg-strip-int-night",
  "EXT-DAWN": "bg-strip-ext-night",
  "INT-DUSK": "bg-strip-int-night",
  "EXT-DUSK": "bg-strip-ext-night",
};

function stripColour(scene: SceneStrip): string {
  return STRIP_COLOUR[`${scene.int_ext}-${scene.time_of_day}`] ?? "bg-strip-int-day";
}

/** What a day with no scenes on it is actually for. */
const DAY_KIND_LABEL: Record<string, string> = {
  RESERVE: "Held in reserve",
  TRAVEL: "Travel day",
  COMPANY_MOVE: "Company move",
  HOLD: "On hold",
  SHOOT: "Nothing scheduled",
};

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
        <Panel title="Nothing to show">
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
        <Panel title="Board">
          <EmptyState>Reading the board…</EmptyState>
        </Panel>
      </div>
    );
  }

  const working = schedule.days.filter((day) => day.scenes.length > 0);
  const totalMinutes = working.reduce((sum, day) => sum + day.scheduled_minutes, 0);
  const sceneCount = working.reduce((sum, day) => sum + day.scenes.length, 0);

  return (
    <div className="space-y-5 p-6">
      {/* A production reads as a sentence, not as five boxed statistics. */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-chalk-100">
            {schedule.title}
          </h1>
          <p className="mt-1 text-sm text-chalk-400">
            <span className="tnum text-chalk-200">{working.length}</span> shooting days
            carrying <span className="tnum text-chalk-200">{sceneCount}</span> scenes,{" "}
            <span className="tnum text-chalk-200">{(totalMinutes / 60).toFixed(1)}h</span> of
            scene time, between {shortDate(schedule.shoot_start)} and{" "}
            {shortDate(schedule.shoot_end)}.
          </p>
        </div>

        <div className="flex items-center gap-4">
          <Legend />
          <button type="button" className="btn-quiet" onClick={() => { load(); refreshVersion(); }}>
            Refresh
          </button>
        </div>
      </header>

      <div className="space-y-2">
        {schedule.days.map((day) => (
          <DayBand key={`${day.date}-${day.unit}`} day={day} />
        ))}
      </div>
    </div>
  );
}

/**
 * One shooting day: the header rail, then its strips.
 *
 * The strips are the row. There is no card around them and no shadow beneath
 * them, because on a real board they are simply held in a channel.
 */
function DayBand({ day }: { day: ScheduleDay }) {
  const hours = (
    (new Date(day.wrap_time).getTime() - new Date(day.call_time).getTime()) /
    3_600_000
  ).toFixed(1);
  const empty = day.scenes.length === 0;

  return (
    <article className="board-day overflow-hidden">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3 py-2">
        <h2 className="font-condensed text-base font-semibold text-chalk-100">
          {shortDate(day.date)}
        </h2>
        <span className="min-w-0 flex-1 truncate text-xs text-chalk-400">
          {day.location_name}
        </span>
        {day.unit !== "MAIN" ? (
          <span className="rounded-sm border border-board-500 px-1.5 py-0.5 text-2xs text-chalk-400">
            {day.unit} unit
          </span>
        ) : null}
        <span className="tnum text-sm text-chalk-200">
          {clock(day.call_time)} <span className="text-chalk-600">to</span>{" "}
          {clock(day.wrap_time)}
        </span>
        <span className="tnum w-12 text-right text-xs text-chalk-600">{hours}h</span>
      </div>

      {empty ? (
        // An empty channel, drawn as an empty channel: nothing is boarded here,
        // and the reason it is empty is the useful part.
        <div className="border-t border-board-600 bg-board-800 px-3 py-2.5">
          <span className="text-xs text-chalk-600">
            {DAY_KIND_LABEL[day.day_kind] ?? "Nothing scheduled"}
          </span>
        </div>
      ) : (
        <div className="space-y-px border-t border-board-600 bg-board-600">
          {day.scenes.map((scene) => (
            <Strip key={scene.id} scene={scene} />
          ))}
        </div>
      )}
    </article>
  );
}

function Strip({ scene }: { scene: SceneStrip }) {
  return (
    <div className={`strip ${stripColour(scene)}`}>
      <span className="strip-number">{scene.number}</span>

      {/* Printed as well as coloured. A real strip carries I/E and D/N in ink,
          and a board that encodes them only in colour is unreadable to anyone
          who cannot separate the yellow from the green. */}
      <span className="font-condensed text-2xs font-semibold opacity-60">
        {scene.int_ext} {scene.time_of_day}
      </span>

      <span className="strip-slug flex-1">{scene.description || scene.slug}</span>

      {scene.vfx_plate ? (
        <span className="rounded-sm bg-ink/15 px-1.5 py-0.5 text-2xs font-medium">plate</span>
      ) : null}
      {scene.cast_count > 0 ? (
        <span className="tnum text-2xs opacity-60">{scene.cast_count} cast</span>
      ) : null}
      <span className="tnum w-12 text-right text-2xs opacity-60">
        {scene.estimated_minutes}m
      </span>
    </div>
  );
}

/**
 * The key to the colour code.
 *
 * It earns its place because the colour is data: without it the board is
 * decorative, and with it a viewer who has never seen a strip board can read
 * one in about four seconds.
 */
function Legend() {
  const entries: Array<[string, string]> = [
    ["bg-strip-int-day", "int day"],
    ["bg-strip-ext-day", "ext day"],
    ["bg-strip-int-night", "int night"],
    ["bg-strip-ext-night", "ext night"],
  ];
  return (
    <div className="flex items-center gap-3">
      {entries.map(([colour, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className={`h-3 w-5 rounded-[1px] ${colour}`} aria-hidden="true" />
          <span className="text-2xs text-chalk-600">{label}</span>
        </span>
      ))}
    </div>
  );
}

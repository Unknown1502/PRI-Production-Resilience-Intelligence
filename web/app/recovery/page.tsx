"use client";

/**
 * Counterfactual Recovery — the screen the project is judged on.
 *
 * Three things share it: the replanning timeline (where the rejection stays
 * visible), the plan grid with the Pareto frontier, and Gemini's explanation
 * with its real tool-call trace. Everything on it is a number the engine
 * computed.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { PRODUCTION_ID, api, money } from "@/lib/api";
import { lastFrame } from "@/lib/stream";
import type { Candidate, ImpactReport, Recovery } from "@/lib/types";

import { PlanCard } from "@/components/PlanCard";
import { useShell } from "@/components/Shell";
import { Timeline } from "@/components/Timeline";
import { EmptyState, Panel, StatusPill } from "@/components/primitives";

const APPROVER = "producer@nighttrain";

/**
 * Shown in place of Gemini's explanation on a restored view.
 *
 * The candidates and violations below it are real, stored records. The
 * reasoning is not stored, and saying so is better than an empty panel that
 * looks like the model failed.
 */
const RESTORED_NOTE = [
  "Restored from the last recovery session. The plans and the rule violations",
  "below are exactly as they were stored.",
  "",
  "Run the disruption again to see Gemini's reasoning and its tool calls.",
].join("\n");

/** A restored session carries no impact report; the screen does not read it. */
const EMPTY_IMPACT: ImpactReport = {
  event_id: "",
  directly_affected_scene_ids: [],
  downstream_scene_ids: [],
  affected_cast_ids: [],
  affected_crew_ids: [],
  affected_location_ids: [],
  affected_equipment_ids: [],
  affected_days: [],
  downstream_dependency_count: 0,
  blast_radius: 0,
};

export default function RecoveryPage() {
  const { frames, setMode, refreshVersion } = useShell();
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const eventFrame = lastFrame(frames, "EVENT_RECEIVED");
  const eventId = eventFrame ? String(eventFrame.event_id ?? "") : "";

  // The stream tells us a recovery happened; the API has the numbers. Fetching
  // on AWAITING_APPROVAL means the grid fills exactly when the run finished.
  const awaiting = lastFrame(frames, "AWAITING_APPROVAL");

  const runRecovery = useCallback(
    async (withEventId: string) => {
      if (!withEventId) return;
      setBusy(true);
      setNotice(null);
      try {
        const result = await api.recover(PRODUCTION_ID, withEventId);
        setRecovery(result);
        setMode(result.mode);
        setSelected(result.recommended_plan_id);
      } catch (cause) {
        setNotice(cause instanceof Error ? cause.message : "Recovery failed.");
      } finally {
        setBusy(false);
      }
    },
    [setMode],
  );

  useEffect(() => {
    if (awaiting !== null && recovery === null && eventId) {
      void runRecovery(eventId);
    }
  }, [awaiting, recovery, eventId, runRecovery]);

  // Rehydrate from the last stored session on mount.
  //
  // Everything above is driven by the SSE stream, which means the screen was
  // blank for anyone who refreshed, opened it in a second tab, or simply
  // arrived after the disruption — while a finished recovery sat in the
  // database. During a live demo that is indistinguishable from a broken app,
  // and it is the single easiest way to lose the room.
  //
  // The restored view is deliberately partial: the candidates and their
  // violations are stored, Gemini's explanation and tool trace are not. It says
  // so rather than pretending otherwise, and a fresh run replaces it.
  useEffect(() => {
    if (recovery !== null) return;
    let live = true;

    api
      .latestSession(PRODUCTION_ID)
      .then(({ session }) => {
        if (!live || session === null || session.candidates.length === 0) return;
        setRecovery({
          session_id: session.session_id,
          production_id: session.production_id,
          event_id: session.event_id ?? "",
          base_version: session.base_version,
          impact: EMPTY_IMPACT,
          candidates: session.candidates,
          rounds: [],
          pareto_plan_ids: session.candidates
            .filter((candidate) => candidate.pareto_optimal)
            .map((candidate) => candidate.id),
          recommended_plan_id:
            session.candidates.find((candidate) => candidate.pareto_optimal && candidate.valid)
              ?.id ?? null,
          explanation: RESTORED_NOTE,
          tradeoff_summary: "",
          mode: "deterministic",
          tool_calls: [],
        });
        setSelected(
          session.candidates.find((candidate) => candidate.pareto_optimal && candidate.valid)?.id ??
            null,
        );
      })
      .catch(() => {
        // A production with no history is the normal empty state, not an error.
      });

    return () => {
      live = false;
    };
  }, [recovery]);

  const approve = useCallback(async () => {
    if (recovery === null || selected === null) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.approve(recovery.session_id, selected, APPROVER, "Approved from the console.");
      setNotice(`${selected} approved. Execute it from Governance.`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "Approval failed.");
    } finally {
      setBusy(false);
      refreshVersion();
    }
  }, [recovery, selected, refreshVersion]);

  const currency = "USD";
  const scored = useMemo(
    () => (recovery?.candidates ?? []).filter((c) => c.score !== null),
    [recovery],
  );

  return (
    <div className="grid h-full grid-cols-1 gap-4 p-6 2xl:grid-cols-[minmax(0,1fr)_400px]">
      <div className="flex min-w-0 flex-col gap-4">
        <Panel
          title="Replanning"
          actions={
            eventId ? (
              <button
                type="button"
                className="btn-quiet"
                onClick={() => runRecovery(eventId)}
                disabled={busy}
              >
                {busy ? "Working…" : "Re-run recovery"}
              </button>
            ) : null
          }
        >
          <div className="max-h-[340px] overflow-y-auto">
            <Timeline frames={frames} />
          </div>
        </Panel>

        {recovery === null ? (
          <Panel title="Candidates">
            <EmptyState>
              No recovery run yet. Publish a disruption and the candidates appear here.
            </EmptyState>
          </Panel>
        ) : (
          <>
            <Panel
              title={`Candidates — frontier ${recovery.pareto_plan_ids.join(", ") || "none"}`}
              actions={
                <span className="tnum text-2xs text-chalk-600">
                  session {recovery.session_id}
                </span>
              }
            >
              <div className="grid gap-3 p-3 lg:grid-cols-2">
                {recovery.candidates.map((candidate) => (
                  <PlanCard
                    key={candidate.id}
                    candidate={candidate}
                    currency={currency}
                    recommended={candidate.id === recovery.recommended_plan_id}
                    selected={candidate.id === selected}
                    onSelect={setSelected}
                  />
                ))}
              </div>
            </Panel>

            <Panel title="Trade-off frontier">
              <ParetoScatter candidates={scored} currency={currency} />
            </Panel>
          </>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        <Panel
          title={recovery?.mode === "agent" ? "Gemini" : "Explanation"}
          actions={
            recovery ? (
              <StatusPill tone={recovery.mode === "agent" ? "clear" : "caution"}>
                {recovery.mode}
              </StatusPill>
            ) : null
          }
        >
          {recovery === null ? (
            <EmptyState>Nothing to explain yet.</EmptyState>
          ) : (
            <div className="space-y-3 px-4 py-3">
              {recovery.explanation.split("\n\n").map((paragraph, index) => (
                <p key={index} className="text-xs leading-relaxed text-chalk-200">
                  {paragraph}
                </p>
              ))}
            </div>
          )}
        </Panel>

        {recovery && recovery.tool_calls.length > 0 ? (
          <Panel title="Tool calls">
            <ol className="divide-y divide-board-600">
              {recovery.tool_calls.map((call, index) => (
                <li key={`${call.name}-${index}`} className="px-4 py-2.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="tnum text-xs text-chalk-100">{call.name}</span>
                    <StatusPill tone={call.ok ? "clear" : "alert"}>
                      {call.ok ? "ok" : "error"}
                    </StatusPill>
                  </div>
                  <p className="mt-1 text-2xs text-chalk-600">{call.summary}</p>
                  {Object.keys(call.arguments).length > 0 ? (
                    <pre className="tnum mt-1.5 overflow-x-auto rounded bg-board-900 p-2 text-2xs text-chalk-400">
                      {JSON.stringify(call.arguments)}
                    </pre>
                  ) : null}
                </li>
              ))}
            </ol>
          </Panel>
        ) : null}

        <Panel title="Approval">
          <div className="space-y-3 px-4 py-3">
            <p className="text-xs text-chalk-400">
              {selected ? (
                <>
                  Requesting approval for{" "}
                  <span className="tnum text-chalk-100">{selected}</span> as{" "}
                  <span className="text-chalk-100">{APPROVER}</span>.
                </>
              ) : (
                "Select a valid plan to request approval."
              )}
            </p>
            <button
              type="button"
              className="btn-go w-full justify-center"
              onClick={approve}
              disabled={busy || selected === null}
            >
              Request approval
            </button>
            {notice ? <p className="text-2xs text-caution">{notice}</p> : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

/**
 * Cost against delay, with the frontier connected.
 *
 * Hand-drawn SVG rather than a charting dependency: four points and a line is
 * not worth 90kB, and this way the axes can be labelled in the same units the
 * plan cards use.
 */
function ParetoScatter({
  candidates,
  currency,
}: {
  candidates: Candidate[];
  currency: string;
}) {
  if (candidates.length === 0) {
    return <EmptyState>No scored candidates.</EmptyState>;
  }

  const width = 520;
  const height = 220;
  const pad = { left: 62, right: 18, top: 16, bottom: 34 };

  const points = candidates.map((candidate) => ({
    candidate,
    delay: candidate.score?.schedule_delay_days ?? 0,
    cost: Number.parseFloat(candidate.score?.incremental_cost ?? "0"),
  }));

  const maxDelay = Math.max(...points.map((p) => p.delay), 1);
  const maxCost = Math.max(...points.map((p) => p.cost), 1);

  const x = (delay: number) =>
    pad.left + (delay / maxDelay) * (width - pad.left - pad.right);
  const y = (cost: number) =>
    height - pad.bottom - (cost / maxCost) * (height - pad.top - pad.bottom);

  const frontier = points
    .filter((p) => p.candidate.pareto_optimal)
    .sort((a, b) => a.delay - b.delay);

  return (
    <div className="overflow-x-auto p-3">
      <svg width={width} height={height} role="img" aria-label="Cost against delay">
        <line
          x1={pad.left}
          y1={height - pad.bottom}
          x2={width - pad.right}
          y2={height - pad.bottom}
          stroke="#252b38"
        />
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} stroke="#252b38" />

        <text x={width - pad.right} y={height - 8} textAnchor="end" fontSize="10" fill="#69738a">
          schedule delay (days) →
        </text>
        <text
          x={10}
          y={pad.top + 6}
          fontSize="10"
          fill="#69738a"
          transform={`rotate(-90 10 ${pad.top + 6})`}
          textAnchor="end"
        >
          ← incremental cost
        </text>

        {frontier.length > 1 ? (
          <polyline
            points={frontier.map((p) => `${x(p.delay)},${y(p.cost)}`).join(" ")}
            fill="none"
            stroke="#3ddc97"
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
        ) : null}

        {points.map(({ candidate, delay, cost }) => {
          const invalid = !candidate.valid;
          const colour = invalid ? "#ff6b4a" : candidate.pareto_optimal ? "#3ddc97" : "#69738a";
          return (
            <g key={candidate.id}>
              <circle
                cx={x(delay)}
                cy={y(cost)}
                r={candidate.pareto_optimal ? 6 : 4.5}
                fill={colour}
                fillOpacity={invalid ? 0.35 : 0.9}
                stroke={colour}
              />
              <text
                x={x(delay) + 10}
                y={y(cost) + 4}
                fontSize="11"
                fill={colour}
                fontFamily="ui-monospace, monospace"
              >
                {candidate.label}
              </text>
            </g>
          );
        })}
      </svg>

      <p className="mt-2 text-2xs text-chalk-600">
        Frontier in green; dominated plans grey; rejected plans hollow. Maximum cost on this
        chart is {money(maxCost, currency)}.
      </p>
    </div>
  );
}

"use client";

/**
 * One candidate plan.
 *
 * A dominated plan is dimmed rather than hidden: the point of showing the
 * frontier is that a producer can see what they are not choosing and why.
 * An invalid plan keeps its violation card at full contrast, because the
 * rejection is evidence, not an error to tidy away.
 */

import { money } from "@/lib/api";
import type { Candidate } from "@/lib/types";

import { RuleViolationCard, StatusPill } from "./primitives";

export function PlanCard({
  candidate,
  currency,
  recommended,
  selected,
  onSelect,
}: {
  candidate: Candidate;
  currency: string;
  recommended?: boolean;
  selected?: boolean;
  onSelect?: (planId: string) => void;
}) {
  const { score, valid, pareto_optimal: pareto } = candidate;
  const dominated = valid && !pareto;
  const hardViolation = candidate.violations.find((v) => v.severity === "HARD");

  return (
    <article
      className={`panel flex flex-col transition-all ${
        dominated ? "opacity-55 hover:opacity-90" : ""
      } ${selected ? "ring-1 ring-clear" : ""} ${
        !valid ? "border-alert-dim" : pareto ? "border-clear-dim" : ""
      }`}
    >
      <header className="panel-header">
        <div className="flex items-baseline gap-2.5">
          <h3 className="tnum text-base font-bold text-chalk-100">Plan {candidate.label}</h3>
          {candidate.family ? (
            <span className="field-label">{candidate.family}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          {recommended ? <StatusPill tone="clear">recommended</StatusPill> : null}
          {pareto ? <StatusPill tone="clear">pareto</StatusPill> : null}
          {dominated ? <StatusPill>dominated</StatusPill> : null}
          <StatusPill tone={valid ? "clear" : "alert"} pulse={!valid}>
            {valid ? "valid" : "invalid"}
          </StatusPill>
        </div>
      </header>

      <div className="grid grid-cols-3 gap-4 px-4 py-3">
        <Figure
          label="delay"
          value={score ? score.schedule_delay_days.toFixed(2) : "—"}
          unit="d"
        />
        <Figure
          label="cost"
          value={score ? money(score.incremental_cost, currency) : "—"}
        />
        <Figure
          label="risk"
          value={score ? score.operational_risk.toFixed(2) : "—"}
          tone={score && score.operational_risk > 0.35 ? "alert" : "neutral"}
        />
      </div>

      <div className="grid grid-cols-3 gap-4 border-t border-ink-700 px-4 py-2.5">
        <Figure small label="scenes" value={score?.affected_scene_count ?? "—"} />
        <Figure
          small
          label="crew hrs"
          value={score ? score.crew_disruption_hours.toFixed(1) : "—"}
        />
        <Figure small label="downstream" value={score?.downstream_dependency_impact ?? "—"} />
      </div>

      <div className="border-t border-ink-700 px-4 py-3">
        <p className="field-label mb-1.5">moves</p>
        <ol className="space-y-1">
          {candidate.moves.map((move, index) => (
            <li key={`${candidate.id}-move-${index}`} className="flex gap-2 text-xs">
              <span className="tnum text-chalk-600">{index + 1}</span>
              <span className="text-chalk-200">{move.summary}</span>
            </li>
          ))}
        </ol>
      </div>

      {hardViolation ? (
        <div className="border-t border-ink-700 p-3">
          <RuleViolationCard
            code={hardViolation.code}
            message={hardViolation.message}
            observed={hardViolation.observed}
            required={hardViolation.required}
          />
        </div>
      ) : null}

      {onSelect && valid ? (
        <footer className="mt-auto border-t border-ink-700 px-4 py-3">
          <button
            type="button"
            className={selected ? "btn-go w-full justify-center" : "btn-quiet w-full justify-center"}
            onClick={() => onSelect(candidate.id)}
          >
            {selected ? "Selected for approval" : "Select this plan"}
          </button>
        </footer>
      ) : null}
    </article>
  );
}

function Figure({
  label,
  value,
  unit,
  tone = "neutral",
  small = false,
}: {
  label: string;
  value: string | number;
  unit?: string;
  tone?: "neutral" | "alert";
  small?: boolean;
}) {
  return (
    <div>
      <p className="field-label">{label}</p>
      <p
        className={`tnum ${small ? "text-xs" : "text-sm"} ${
          tone === "alert" ? "text-alert" : "text-chalk-100"
        }`}
      >
        {value}
        {unit ? <span className="ml-0.5 text-chalk-600">{unit}</span> : null}
      </p>
    </div>
  );
}

/**
 * The small pieces every screen is built from.
 *
 * Two signal colours only — `alert` for a refusal, `clear` for validated and
 * approved — and tabular numerals on every figure, so a column of costs reads
 * as a column. Everything else is weight and spacing on the board's own
 * palette, because a screen where six things are coloured is a screen where
 * nothing reads as urgent.
 */

import type { ReactNode } from "react";

export type Tone = "neutral" | "alert" | "clear" | "caution";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-board-500 bg-board-700 text-chalk-400",
  alert: "border-stamp-dim bg-stamp-wash text-stamp",
  clear: "border-seal-dim bg-seal-wash text-seal",
  caution: "border-caution/30 bg-caution/10 text-caution",
};

export function StatusPill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-2xs
                  font-medium ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}

export function MetricCell({
  label,
  value,
  unit,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string | number;
  unit?: string;
  tone?: Tone;
  hint?: string;
}) {
  const valueTone =
    tone === "alert" ? "text-stamp" : tone === "clear" ? "text-seal" : "text-chalk-100";
  return (
    <div className="flex flex-col gap-1">
      <span className="field-label">{label}</span>
      <span className={`tnum text-xl leading-none ${valueTone}`}>
        {value}
        {unit ? <span className="ml-1 text-xs text-chalk-600">{unit}</span> : null}
      </span>
      {hint ? <span className="text-2xs text-chalk-600">{hint}</span> : null}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  className = "",
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title ? (
        <header className="panel-header">
          <h2 className="panel-title">{title}</h2>
          {actions}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[120px] items-center justify-center px-6 py-8 text-center text-xs text-chalk-600">
      {children}
    </div>
  );
}

/**
 * The rejected-plan card.
 *
 * Deliberately the loudest thing on the recovery screen. `observed` and
 * `required` are printed verbatim from the API because the demo narration
 * reads them aloud, and a UI that reformats "9.0h" into "9 hours" makes the
 * narrator wrong.
 */
export function RuleViolationCard({
  code,
  message,
  observed,
  required,
  pulse = false,
}: {
  code: string;
  message: string;
  observed: string;
  required: string;
  pulse?: boolean;
}) {
  return (
    <div className="relative rounded-sm border border-stamp-dim bg-stamp-wash p-3 pr-24">
      {/* The rule code, set as the stamp that was pressed onto the plan. It is
          the one piece of theatre in the interface, and it is here because
          this is the moment the whole demo turns on: software refusing a plan
          and saying, in its own words, exactly why. */}
      <span
        className={`pointer-events-none absolute right-3 top-3 select-none rounded-sm border-2
                    border-stamp px-2 py-0.5 font-condensed text-xl font-bold tracking-wide
                    text-stamp opacity-75 ${pulse ? "animate-stamp-in" : ""}`}
        style={{ transform: "rotate(-2deg)" }}
        aria-hidden="true"
      >
        {code}
      </span>

      <p className="text-2xs text-stamp">Refused by {code}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-chalk-200">{message}</p>

      <dl className="mt-3 flex gap-6 border-t border-stamp-dim/50 pt-2.5">
        <div>
          <dt className="field-label">observed</dt>
          <dd className="tnum text-base text-stamp">{observed}</dd>
        </div>
        <div>
          <dt className="field-label">required</dt>
          <dd className="tnum text-base text-chalk-200">{required}</dd>
        </div>
      </dl>
    </div>
  );
}

export function CheckRow({
  code,
  name,
  passed,
  detail,
}: {
  code: string;
  name: string;
  passed: boolean;
  detail: string;
}) {
  return (
    <li className="flex gap-3 border-b border-board-600 px-4 py-3 last:border-b-0">
      <span
        className={`tnum mt-0.5 flex h-6 w-8 shrink-0 items-center justify-center rounded text-2xs
                    font-bold ${
                      passed
                        ? "bg-seal-wash text-seal"
                        : "bg-stamp-wash text-stamp"
                    }`}
      >
        {code}
      </span>
      <div className="min-w-0">
        <p className="text-xs font-medium text-chalk-100">{name.replace(/_/g, " ")}</p>
        <p className="mt-0.5 text-2xs leading-relaxed text-chalk-600">{detail}</p>
      </div>
      <span className={`ml-auto shrink-0 text-xs ${passed ? "text-seal" : "text-stamp"}`}>
        {passed ? "Passed" : "Failed"}
      </span>
    </li>
  );
}

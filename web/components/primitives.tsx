/**
 * The small pieces every screen is built from.
 *
 * Two signal colours only — `alert` for impact and violations, `clear` for
 * validated and approved — and monospaced tabular numerals on every figure, so
 * a column of costs reads as a column.
 */

import type { ReactNode } from "react";

export type Tone = "neutral" | "alert" | "clear" | "caution";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-ink-600 bg-ink-800 text-chalk-400",
  alert: "border-alert-dim bg-alert-wash text-alert",
  clear: "border-clear-dim bg-clear-wash text-clear",
  caution: "border-caution/30 bg-caution/10 text-caution",
};

export function StatusPill({
  children,
  tone = "neutral",
  pulse = false,
}: {
  children: ReactNode;
  tone?: Tone;
  pulse?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs
                  font-semibold uppercase tracking-[0.1em] ${TONE_CLASSES[tone]}
                  ${pulse ? "animate-pulse-alert" : ""}`}
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
    tone === "alert" ? "text-alert" : tone === "clear" ? "text-clear" : "text-chalk-100";
  return (
    <div className="flex flex-col gap-1">
      <span className="field-label">{label}</span>
      <span className={`tnum text-lg leading-none ${valueTone}`}>
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
    <div
      className={`rounded-md border border-alert-dim bg-alert-wash p-3 ${
        pulse ? "animate-pulse-alert" : ""
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="tnum rounded bg-alert px-1.5 py-0.5 text-2xs font-bold text-ink-900">
          {code}
        </span>
        <span className="text-2xs font-semibold uppercase tracking-[0.1em] text-alert">
          constraint violated
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-chalk-200">{message}</p>
      <dl className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-alert-dim/50 pt-2.5">
        <div>
          <dt className="field-label">observed</dt>
          <dd className="tnum text-sm text-alert">{observed}</dd>
        </div>
        <div>
          <dt className="field-label">required</dt>
          <dd className="tnum text-sm text-chalk-200">{required}</dd>
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
    <li className="flex gap-3 border-b border-ink-700 px-4 py-3 last:border-b-0">
      <span
        className={`tnum mt-0.5 flex h-6 w-8 shrink-0 items-center justify-center rounded text-2xs
                    font-bold ${
                      passed
                        ? "bg-clear-wash text-clear"
                        : "bg-alert-wash text-alert"
                    }`}
      >
        {code}
      </span>
      <div className="min-w-0">
        <p className="text-xs font-medium text-chalk-100">{name.replace(/_/g, " ")}</p>
        <p className="mt-0.5 text-2xs leading-relaxed text-chalk-600">{detail}</p>
      </div>
      <span className={`ml-auto shrink-0 text-xs ${passed ? "text-clear" : "text-alert"}`}>
        {passed ? "PASS" : "FAIL"}
      </span>
    </li>
  );
}

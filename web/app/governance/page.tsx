"use client";

/**
 * Governance — the policy, the approval, and the seven gates.
 *
 * The checklist is the point. Architecture law 4 says no consequential
 * mutation may bypass the sequence, and this screen is where that claim
 * becomes visible: each step lights up only when the transition service
 * reports it, and a refusal stops the row it was refused at.
 */

import { useCallback, useEffect, useState } from "react";

import { PriApiError, api, money } from "@/lib/api";
import { lastFrame } from "@/lib/stream";
import { TRANSITION_STEPS, type Candidate, type ExecuteResult } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { EmptyState, Panel, StatusPill } from "@/components/primitives";

const APPROVER = "producer@nighttrain";

const STEP_LABEL: Record<string, string> = {
  validate_state: "Validate state — head version matches the plan's base",
  validate_constraints: "Validate constraints — re-run the validator, not a cached verdict",
  validate_policy: "Validate policy — does this change require approval?",
  verify_authorization: "Verify authorization — is this approver permitted?",
  request_approval: "Request approval — an APPROVED row must exist",
  execute: "Execute — commit atomically, regenerate call sheets",
  verify_result: "Verify result — six checks, or the write is rolled back",
};

const POLICY_ROWS = [
  ["C001", "crew_turnaround", "minimum 10.0 hours between wrap and next call"],
  ["C002", "max_daily_hours", "maximum 12.0 hours from call to wrap"],
  ["C003", "cast_availability", "no scene inside a cast unavailable window"],
  ["C004", "location_permit", "the day must fit inside a permit window"],
  ["C005", "location_double_book", "two units may not share a location on a day"],
  ["C006", "equipment_window", "equipment used only inside its rental window"],
  ["C007", "equipment_double_book", "same equipment, two units, same day"],
  ["C008", "prerequisite_order", "a prerequisite must shoot on a strictly earlier date"],
  ["C009", "int_ext_match", "scene INT/EXT supported by its location"],
  ["C010", "time_of_day_match", "scene time of day supported by its location"],
] as const;

export default function GovernancePage() {
  const { frames, refreshVersion } = useShell();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [approver, setApprover] = useState(APPROVER);
  const [note, setNote] = useState("");
  const [result, setResult] = useState<ExecuteResult | null>(null);
  const [failedStep, setFailedStep] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const awaiting = lastFrame(frames, "AWAITING_APPROVAL");
  const streamSession = awaiting ? String(awaiting.session_id ?? "") : "";

  const load = useCallback(async (id: string) => {
    if (!id) return;
    try {
      const body = (await api.session(id)) as {
        candidates?: Array<Record<string, unknown>>;
      };
      const rows = body.candidates ?? [];
      setCandidates(
        rows.map((row) => ({
          id: String(row.id),
          label: String(row.label),
          family: null,
          valid: Boolean(row.valid),
          pareto_optimal: Boolean(row.pareto_optimal),
          moves: [],
          violations: [],
          score: (row.score as Candidate["score"]) ?? null,
          resulting_state_digest: null,
        })),
      );
      setSessionId(id);
    } catch {
      setCandidates([]);
    }
  }, []);

  useEffect(() => {
    if (streamSession) void load(streamSession);
  }, [streamSession, load]);

  useEffect(() => {
    if (awaiting?.recommended_plan_id) setSelected(String(awaiting.recommended_plan_id));
  }, [awaiting]);

  const decide = useCallback(
    async (decision: "APPROVED" | "REJECTED") => {
      if (sessionId === null || selected === null) return;
      setBusy(true);
      setNotice(null);
      try {
        if (decision === "APPROVED") {
          await api.approve(sessionId, selected, approver, note || undefined);
          setNotice(`${selected} approved. Execute when ready.`);
        } else {
          await api.reject(sessionId, selected, approver, note || undefined);
          setNotice(`${selected} rejected.`);
        }
      } catch (cause) {
        setNotice(cause instanceof Error ? cause.message : "Decision failed.");
      } finally {
        setBusy(false);
      }
    },
    [sessionId, selected, approver, note],
  );

  const execute = useCallback(async () => {
    if (sessionId === null || selected === null) return;
    setBusy(true);
    setNotice(null);
    setFailedStep(null);
    try {
      const outcome = await api.execute(sessionId, selected, approver);
      setResult(outcome);
      setNotice(
        outcome.replayed
          ? `Already executed — replaying version ${outcome.new_version}.`
          : `Committed as version ${outcome.new_version}.`,
      );
      refreshVersion();
    } catch (cause) {
      if (cause instanceof PriApiError) {
        setFailedStep(cause.step);
        setNotice(
          cause.ruleCode
            ? `Stopped at ${cause.step}: ${cause.ruleCode} — ${cause.message}`
            : `Stopped at ${cause.step ?? "an unnamed step"}: ${cause.message}`,
        );
      } else {
        setNotice(cause instanceof Error ? cause.message : "Execution failed.");
      }
    } finally {
      setBusy(false);
    }
  }, [sessionId, selected, approver, refreshVersion]);

  const completed = new Set(result?.steps_completed ?? []);
  const chosen = candidates.find((c) => c.id === selected) ?? null;

  return (
    <div className="grid grid-cols-1 gap-4 p-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex flex-col gap-4">
        <Panel title="Execution sequence">
          <ol className="divide-y divide-ink-700">
            {TRANSITION_STEPS.map((step, index) => {
              const done = completed.has(step);
              const failed = failedStep === step;
              return (
                <li key={step} className="flex items-start gap-3 px-4 py-2.5">
                  <span
                    className={`tnum mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center
                                rounded-full border text-2xs font-bold ${
                                  failed
                                    ? "border-alert bg-alert-wash text-alert"
                                    : done
                                      ? "border-clear bg-clear-wash text-clear"
                                      : "border-ink-600 bg-ink-800 text-chalk-600"
                                }`}
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0">
                    <p
                      className={`text-xs ${
                        failed ? "text-alert" : done ? "text-chalk-100" : "text-chalk-600"
                      }`}
                    >
                      {STEP_LABEL[step]}
                    </p>
                  </div>
                  <span className="ml-auto shrink-0">
                    {failed ? (
                      <StatusPill tone="alert">refused</StatusPill>
                    ) : done ? (
                      <StatusPill tone="clear">passed</StatusPill>
                    ) : (
                      <StatusPill>pending</StatusPill>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
        </Panel>

        <Panel title="Active policy — config/policies.yaml">
          <table className="w-full text-xs">
            <tbody>
              {POLICY_ROWS.map(([code, name, rule]) => (
                <tr key={code} className="row-hover border-b border-ink-700 last:border-b-0">
                  <td className="tnum w-16 px-4 py-2 text-chalk-400">{code}</td>
                  <td className="w-52 py-2 text-chalk-100">{name}</td>
                  <td className="px-4 py-2 text-chalk-600">{rule}</td>
                  <td className="w-20 px-4 py-2 text-right">
                    <StatusPill tone="clear">active</StatusPill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <Panel title="Approval">
        {candidates.length === 0 ? (
          <EmptyState>
            No open recovery session. Run a disruption first — the candidates
            appear here for approval.
          </EmptyState>
        ) : (
          <div className="space-y-3 px-4 py-3">
            <div>
              <label className="field-label" htmlFor="plan">
                plan
              </label>
              <select
                id="plan"
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-chalk-100"
                value={selected ?? ""}
                onChange={(event) => setSelected(event.target.value)}
              >
                <option value="">Select a plan…</option>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id} disabled={!candidate.valid}>
                    Plan {candidate.label}
                    {candidate.valid ? "" : " — invalid"}
                    {candidate.pareto_optimal ? " (pareto)" : ""}
                  </option>
                ))}
              </select>
            </div>

            {chosen?.score ? (
              <dl className="grid grid-cols-3 gap-2 rounded-md border border-ink-600 bg-ink-800 p-2.5">
                <Summary label="delay" value={`${chosen.score.schedule_delay_days.toFixed(2)}d`} />
                <Summary label="cost" value={money(chosen.score.incremental_cost)} />
                <Summary label="risk" value={chosen.score.operational_risk.toFixed(2)} />
              </dl>
            ) : null}

            <div>
              <label className="field-label" htmlFor="approver">
                approver
              </label>
              <input
                id="approver"
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-chalk-100"
                value={approver}
                onChange={(event) => setApprover(event.target.value)}
              />
            </div>

            <div>
              <label className="field-label" htmlFor="note">
                note
              </label>
              <textarea
                id="note"
                rows={2}
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-800 px-2 py-1.5 text-xs text-chalk-100"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Why this plan?"
              />
            </div>

            <div className="flex gap-2">
              <button
                type="button"
                className="btn-go flex-1 justify-center"
                disabled={busy || selected === null}
                onClick={() => decide("APPROVED")}
              >
                Approve
              </button>
              <button
                type="button"
                className="btn-stop flex-1 justify-center"
                disabled={busy || selected === null}
                onClick={() => decide("REJECTED")}
              >
                Reject
              </button>
            </div>

            <button
              type="button"
              className="btn-quiet w-full justify-center"
              disabled={busy || selected === null}
              onClick={execute}
            >
              {busy ? "Running the seven steps…" : "Execute approved plan"}
            </button>

            {notice ? (
              <p className={`text-2xs ${failedStep ? "text-alert" : "text-clear"}`}>{notice}</p>
            ) : null}
          </div>
        )}
      </Panel>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="field-label">{label}</dt>
      <dd className="tnum text-xs text-chalk-100">{value}</dd>
    </div>
  );
}

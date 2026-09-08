"use client";

/**
 * Audit — the append-only log.
 *
 * Every gate the transition service passed or refused is a row here, written
 * before the next step ran. A run that dies half way leaves a record of exactly
 * how far it got, which is the difference between an audit trail and a summary.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { AuditEntry } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { RunDisruption } from "@/components/RunDisruption";
import { EmptyState, Panel, StatusPill } from "@/components/primitives";

/**
 * The ledger's own columns, with one row showing the shape of an entry.
 *
 * Marked "example" in its own column and dimmed, because a placeholder that
 * could be read as a real audit record would be far worse than the blank panel
 * it replaces — this is the screen whose entire value is that it is truthful.
 */
function LedgerShape() {
  return (
    <div className="border-t border-board-600">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-board-600 text-left">
            <th className="field-label px-4 py-2">time</th>
            <th className="field-label py-2">actor</th>
            <th className="field-label py-2">action</th>
            <th className="field-label py-2">subject</th>
            <th className="field-label px-4 py-2">detail</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-board-600 align-top opacity-50 last:border-b-0">
            <td className="tnum whitespace-nowrap px-4 py-2 text-chalk-600">--:--:--</td>
            <td className="whitespace-nowrap py-2 text-chalk-600">engine</td>
            <td className="py-2">
              <StatusPill>example</StatusPill>
            </td>
            <td className="tnum py-2 text-chalk-600">plan-B</td>
            <td className="px-4 py-2 text-2xs text-chalk-600">
              candidate_rejected — C001 crew_turnaround, observed 9.0h, required &gt;= 10.0h
            </td>
          </tr>
        </tbody>
      </table>
      <p className="px-4 py-2 text-2xs text-chalk-600">
        An example of the shape only. No entry above was recorded.
      </p>
    </div>
  );
}

function tone(action: string): "clear" | "alert" | "caution" | "neutral" {
  if (action.endsWith("rejected") || action.includes("human_review")) return "alert";
  if (action.endsWith("completed") || action.includes("approved")) return "clear";
  if (action.startsWith("transition.")) return "caution";
  return "neutral";
}

export default function AuditPage() {
  const { productionId, frames } = useShell();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .audit(productionId, 500)
      .then((rows) => {
        setEntries(rows);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [productionId]);

  useEffect(load, [load]);
  useEffect(() => {
    const last = frames[frames.length - 1];
    if (last?.stage === "VERIFIED" || last?.stage === "APPROVED") load();
  }, [frames, load]);

  const actions = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.action))).sort(),
    [entries],
  );
  const visible = filter === "" ? entries : entries.filter((e) => e.action === filter);

  return (
    <div className="p-6">
      <Panel
        title={`Audit log — ${entries.length} entries`}
        actions={
          <div className="flex items-center gap-2">
            <select
              className="rounded-md border border-board-500 bg-board-700 px-2 py-1 text-2xs text-chalk-200"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            >
              <option value="">all actions</option>
              {actions.map((action) => (
                <option key={action} value={action}>
                  {action}
                </option>
              ))}
            </select>
            <button type="button" className="btn-quiet" onClick={load}>
              Refresh
            </button>
          </div>
        }
      >
        {error !== null ? (
          <EmptyState>{error}</EmptyState>
        ) : visible.length === 0 ? (
          <EmptyState action={<RunDisruption />} preview={<LedgerShape />}>
            The ledger is append-only and written by the engine, not by this
            screen: every event received, every plan refused with the rule that
            refused it, every approval and every committed version lands here.
            Nothing has happened to this production yet.
          </EmptyState>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-board-600 text-left">
                <th className="field-label px-4 py-2">time</th>
                <th className="field-label py-2">actor</th>
                <th className="field-label py-2">action</th>
                <th className="field-label py-2">subject</th>
                <th className="field-label px-4 py-2">detail</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((entry, index) => (
                <tr
                  key={`${entry.at}-${index}`}
                  className="row-hover border-b border-board-600 align-top last:border-b-0"
                >
                  <td className="tnum whitespace-nowrap px-4 py-2 text-chalk-600">
                    {entry.at.slice(11, 19)}
                  </td>
                  <td className="whitespace-nowrap py-2 text-chalk-400">{entry.actor}</td>
                  <td className="py-2">
                    <StatusPill tone={tone(entry.action)}>{entry.action}</StatusPill>
                  </td>
                  <td className="tnum max-w-[220px] truncate py-2 text-chalk-400">
                    {entry.subject}
                  </td>
                  <td className="px-4 py-2">
                    <pre className="tnum max-w-[460px] overflow-x-auto whitespace-pre-wrap break-all text-2xs text-chalk-600">
                      {JSON.stringify(entry.detail)}
                    </pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}

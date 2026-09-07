"use client";

/**
 * Review a staged import.
 *
 * The screen a 1st AD sees between uploading their board and PRI owning it.
 * Two things it must keep straight, because they look alike and are not:
 *
 *   Errors are PRI's problem with the file — a missing sheet, an id that
 *   points at nothing. They block, and the fix is in the workbook.
 *
 *   Schedule health is the production's problem with itself — turnaround
 *   breaches, cast conflicts already on the board. They never block. A board
 *   that already breaks a rule is exactly the board PRI exists to help with,
 *   and refusing to import it would be refusing the job.
 *
 * The URL is the review, so it survives a refresh and can be pasted to the
 * producer who has to approve it.
 */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, shortDate } from "@/lib/api";
import type { ImportIssue, ImportReport } from "@/lib/types";

import {
  EmptyState,
  MetricCell,
  Panel,
  RuleViolationCard,
  StatusPill,
} from "@/components/primitives";

type TabId = "errors" | "health" | "preview" | "warnings";

export default function ImportReviewPage() {
  const router = useRouter();
  const params = useParams<{ stagingId: string }>();
  const stagingId = params.stagingId;

  const [report, setReport] = useState<ImportReport | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId | null>(null);
  const [confirmedBy, setConfirmedBy] = useState("");
  const [discarding, setDiscarding] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .importReport(stagingId)
      .then((next) => {
        if (!live) return;
        setReport(next);
        setLoadError(null);
        // Open on whatever the user actually has to deal with.
        setTab((current) =>
          current ??
          (next.errors.length > 0
            ? "errors"
            : next.existing_violations.length > 0
              ? "health"
              : "preview"),
        );
      })
      .catch((cause: Error) => live && setLoadError(cause.message));
    return () => {
      live = false;
    };
  }, [stagingId]);

  const commit = useCallback(async () => {
    if (!report) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await api.commitImport(stagingId, confirmedBy.trim());
      router.push(`/?production=${encodeURIComponent(result.production_id)}`);
    } catch (cause) {
      setActionError((cause as Error).message);
      setBusy(false);
    }
  }, [confirmedBy, report, router, stagingId]);

  const discard = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      await api.rejectImport(stagingId, reason.trim() || "No reason given.", confirmedBy.trim() || "unknown");
      router.push("/import");
    } catch (cause) {
      setActionError((cause as Error).message);
      setBusy(false);
    }
  }, [confirmedBy, reason, router, stagingId]);

  if (loadError !== null) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <Panel title="Review">
          <EmptyState>
            This review could not be loaded ({loadError}). Staged imports expire after 24
            hours —{" "}
            <Link href="/import" className="ml-1 text-chalk-200 underline underline-offset-2">
              upload the board again
            </Link>
            .
          </EmptyState>
        </Panel>
      </div>
    );
  }

  if (report === null) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <Panel title="Review">
          <EmptyState>Loading the review…</EmptyState>
        </Panel>
      </div>
    );
  }

  const settled = report.status === "COMMITTED" || report.status === "REJECTED";
  const violationCount = report.existing_violations.length;

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6 pb-28">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-base font-semibold text-chalk-100">Review this import</h1>
        <span className="tnum truncate text-xs text-chalk-600" title={report.filename}>
          {report.filename}
        </span>
        <span className="ml-auto flex items-center gap-2">
          {report.duplicate_of ? <StatusPill tone="caution">already uploaded</StatusPill> : null}
          <StatusPill tone={statusTone(report)}>{report.status.replace("_", " ")}</StatusPill>
        </span>
      </header>

      <Verdict report={report} />

      <Panel title="What is in the file">
        <div className="grid grid-cols-2 gap-6 px-4 py-4 sm:grid-cols-4 lg:grid-cols-6">
          <MetricCell label="scenes" value={report.summary.scene_count} />
          <MetricCell label="shoot days" value={report.summary.shooting_day_count} />
          <MetricCell label="people" value={report.summary.person_count} />
          <MetricCell label="locations" value={report.summary.location_count} />
          <MetricCell label="equipment" value={report.summary.equipment_count} />
          <MetricCell
            label="units"
            value={report.summary.unit_names.join(" + ") || "—"}
            hint={`${report.summary.shoot_span_days} day span`}
          />
        </div>
      </Panel>

      <Tabs
        active={tab ?? "preview"}
        onSelect={setTab}
        counts={{
          errors: report.errors.length,
          health: violationCount,
          preview: 0,
          warnings: report.warnings.length,
        }}
      />

      {tab === "errors" ? <IssueList issues={report.errors} kind="error" /> : null}
      {tab === "warnings" ? <IssueList issues={report.warnings} kind="warning" /> : null}
      {tab === "health" ? <ScheduleHealth report={report} /> : null}
      {tab === "preview" || tab === null ? <Preview report={report} /> : null}

      {settled ? (
        <SettledFooter report={report} />
      ) : (
        <footer
          className="fixed inset-x-0 bottom-0 border-t border-ink-700 bg-ink-850/95 backdrop-blur"
          // The commit is irreversible in one direction — it creates version 1
          // — so the control for it is pinned and never scrolls off.
        >
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-3 px-6 py-3">
            <label className="flex items-center gap-2 text-2xs uppercase tracking-[0.12em] text-chalk-600">
              confirmed by
              <input
                type="text"
                value={confirmedBy}
                onChange={(event) => setConfirmedBy(event.target.value)}
                placeholder="your name"
                className="w-40 rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-xs
                           normal-case tracking-normal text-chalk-100 placeholder:text-chalk-600
                           focus:border-ink-500 focus:outline-none"
              />
            </label>

            {discarding ? (
              <>
                <input
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="why are you discarding it?"
                  className="min-w-0 flex-1 rounded-md border border-ink-600 bg-ink-800 px-2 py-1
                             text-xs text-chalk-100 placeholder:text-chalk-600
                             focus:border-ink-500 focus:outline-none"
                />
                <button type="button" className="btn-stop" disabled={busy} onClick={discard}>
                  Confirm discard
                </button>
                <button
                  type="button"
                  className="btn-quiet"
                  disabled={busy}
                  onClick={() => setDiscarding(false)}
                >
                  Keep it
                </button>
              </>
            ) : (
              <>
                <span className="min-w-0 flex-1 truncate text-2xs text-chalk-600">
                  {report.can_commit
                    ? violationCount > 0
                      ? `${violationCount} existing violation${violationCount === 1 ? "" : "s"} will be imported as-is — they do not block.`
                      : "Ready. This creates version 1; nothing has been written yet."
                    : `Fix the ${report.errors.length} error${report.errors.length === 1 ? "" : "s"} in the workbook and upload it again.`}
                </span>
                <button
                  type="button"
                  className="btn-quiet"
                  disabled={busy}
                  onClick={() => setDiscarding(true)}
                >
                  Discard
                </button>
                <button
                  type="button"
                  className="btn-go"
                  disabled={busy || !report.can_commit || confirmedBy.trim() === ""}
                  onClick={commit}
                  title={
                    !report.can_commit
                      ? "The workbook still has blocking errors."
                      : confirmedBy.trim() === ""
                        ? "The audit record names whoever confirms the import."
                        : undefined
                  }
                >
                  {busy ? "Importing…" : "Import as version 1"}
                </button>
              </>
            )}
          </div>
          {actionError ? (
            <p className="mx-auto max-w-4xl px-6 pb-2 text-2xs text-alert" role="alert">
              {actionError}
            </p>
          ) : null}
        </footer>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Verdict
// ---------------------------------------------------------------------------

function Verdict({ report }: { report: ImportReport }) {
  if (report.status === "COMMITTED") {
    return (
      <Banner tone="clear">
        Imported as version {report.committed_version} of{" "}
        <span className="tnum">{report.production_id}</span>.
      </Banner>
    );
  }
  if (report.status === "REJECTED") {
    return <Banner tone="caution">This import was discarded. Nothing was written.</Banner>;
  }
  if (!report.can_commit) {
    return (
      <Banner tone="alert">
        {report.errors.length} problem{report.errors.length === 1 ? "" : "s"} stop PRI from
        reading this workbook. Every one names the sheet, the row and the fix.
      </Banner>
    );
  }
  if (report.existing_violations.length > 0) {
    return (
      <Banner tone="caution">
        The file reads cleanly. PRI found {report.existing_violations.length} constraint
        violation{report.existing_violations.length === 1 ? "" : "s"} already in the
        schedule — shown below, and imported as-is.
      </Banner>
    );
  }
  return <Banner tone="clear">The file reads cleanly and the schedule is legal.</Banner>;
}

function Banner({ tone, children }: { tone: "alert" | "clear" | "caution"; children: React.ReactNode }) {
  const classes = {
    alert: "border-alert-dim bg-alert-wash text-alert",
    clear: "border-clear-dim bg-clear-wash text-clear",
    caution: "border-caution/30 bg-caution/10 text-caution",
  }[tone];
  return (
    <p className={`rounded-md border px-4 py-2.5 text-xs leading-relaxed ${classes}`}>
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

const TAB_LABELS: Record<TabId, string> = {
  errors: "Errors",
  health: "Schedule health",
  preview: "Preview",
  warnings: "Warnings",
};

function Tabs({
  active,
  onSelect,
  counts,
}: {
  active: TabId;
  onSelect: (tab: TabId) => void;
  counts: Record<TabId, number>;
}) {
  return (
    <div role="tablist" aria-label="Import review" className="flex gap-1 border-b border-ink-700">
      {(Object.keys(TAB_LABELS) as TabId[]).map((id) => {
        const selected = id === active;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onSelect(id)}
            className={`-mb-px border-b-2 px-3 py-2 text-xs transition-colors ${
              selected
                ? "border-chalk-200 font-medium text-chalk-100"
                : "border-transparent text-chalk-600 hover:text-chalk-200"
            }`}
          >
            {TAB_LABELS[id]}
            {counts[id] > 0 ? (
              <span
                className={`tnum ml-2 rounded px-1.5 py-0.5 text-2xs ${
                  id === "errors"
                    ? "bg-alert-wash text-alert"
                    : id === "health"
                      ? "bg-caution/15 text-caution"
                      : "bg-ink-700 text-chalk-400"
                }`}
              >
                {counts[id]}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Errors and warnings
// ---------------------------------------------------------------------------

function IssueList({ issues, kind }: { issues: ImportIssue[]; kind: "error" | "warning" }) {
  const [copied, setCopied] = useState(false);

  const asText = useMemo(() => issues.map(issueLine).join("\n"), [issues]);

  const copyAll = useCallback(() => {
    void navigator.clipboard
      .writeText(asText)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  }, [asText]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  if (issues.length === 0) {
    return (
      <Panel title={kind === "error" ? "Errors" : "Warnings"}>
        <EmptyState>
          {kind === "error"
            ? "No errors. PRI read every sheet it needed."
            : "No warnings. Nothing in the file needed a second look."}
        </EmptyState>
      </Panel>
    );
  }

  return (
    <Panel
      title={kind === "error" ? "Errors" : "Warnings"}
      actions={
        // A 1st AD fixing forty rows works in Excel with this list beside them,
        // not by scrolling a browser tab.
        <button type="button" className="btn-quiet" onClick={copyAll}>
          {copied ? "Copied" : `Copy all ${issues.length}`}
        </button>
      }
    >
      <ul className="divide-y divide-ink-700">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${issue.sheet}-${issue.row}-${index}`} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`tnum rounded px-1.5 py-0.5 text-2xs font-bold ${
                  kind === "error" ? "bg-alert text-ink-900" : "bg-caution text-ink-900"
                }`}
              >
                {issue.code}
              </span>
              <span className="tnum text-2xs text-chalk-600">{location(issue)}</span>
              {issue.offending_value ? (
                <span
                  className="tnum truncate rounded bg-ink-800 px-1.5 py-0.5 text-2xs text-chalk-400"
                  title={issue.offending_value}
                >
                  {issue.offending_value}
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-chalk-100">{issue.message}</p>
            <p className="mt-1 text-2xs leading-relaxed text-chalk-400">→ {issue.fix_hint}</p>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/** `scenes!B7` — the coordinate a user can type into Excel's name box. */
function location(issue: ImportIssue): string {
  if (issue.sheet === null) return "the file";
  const row = issue.row === null ? "" : ` row ${issue.row}`;
  const column = issue.column === null ? "" : `, column ${issue.column}`;
  return `${issue.sheet}${row}${column}`;
}

function issueLine(issue: ImportIssue): string {
  return `[${issue.code}] ${location(issue)} — ${issue.message} Fix: ${issue.fix_hint}`;
}

// ---------------------------------------------------------------------------
// Schedule health
// ---------------------------------------------------------------------------

function ScheduleHealth({ report }: { report: ImportReport }) {
  const codes = Object.entries(report.violation_counts_by_code).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  if (report.existing_violations.length === 0) {
    return (
      <Panel title="Schedule health">
        <EmptyState>
          Every constraint PRI checks already holds on this board. Nothing to flag.
        </EmptyState>
      </Panel>
    );
  }

  return (
    <Panel title="Schedule health">
      <p className="border-b border-ink-700 px-4 py-2.5 text-2xs leading-relaxed text-chalk-400">
        These are breaches in the schedule as delivered, not problems with the file. They
        are imported unchanged — PRI records the production it was actually given — and
        they do not block the import.
      </p>

      <div className="flex flex-wrap gap-2 border-b border-ink-700 px-4 py-3">
        {codes.map(([code, count]) => (
          <span
            key={code}
            className="tnum rounded border border-caution/30 bg-caution/10 px-2 py-0.5 text-2xs text-caution"
          >
            {code} × {count}
          </span>
        ))}
      </div>

      <div className="space-y-2 p-3">
        {report.existing_violations.map((violation, index) => (
          <RuleViolationCard
            key={`${violation.code}-${violation.subject_ids.join("-")}-${index}`}
            code={violation.code}
            message={violation.message}
            observed={violation.observed}
            required={violation.required}
          />
        ))}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Preview
// ---------------------------------------------------------------------------

function Preview({ report }: { report: ImportReport }) {
  const { summary } = report;
  return (
    <Panel title="Preview">
      <dl className="divide-y divide-ink-700">
        <Row label="Shooting window">
          {summary.first_shoot_date && summary.last_shoot_date
            ? `${shortDate(summary.first_shoot_date)} → ${shortDate(summary.last_shoot_date)} (${summary.shoot_span_days} days)`
            : "No shooting days in the file"}
        </Row>
        <Row label="Units">{summary.unit_names.join(", ") || "—"}</Row>
        <Row label="Scenes">{`${summary.scene_count} across ${summary.location_count} location${summary.location_count === 1 ? "" : "s"}`}</Row>
        <Row label="Cast and crew">{`${summary.person_count} people`}</Row>
        <Row label="Equipment">{`${summary.equipment_count} items`}</Row>
        <Row label="Uploaded">{new Date(report.uploaded_at).toLocaleString("en-GB")}</Row>
        <Row label="Staging id">
          <span className="tnum">{report.staging_id}</span>
        </Row>
      </dl>
      <p className="border-t border-ink-700 px-4 py-2.5 text-2xs leading-relaxed text-chalk-600">
        The parsed workbook is held for 24 hours and then deleted. The uploaded file itself
        is never stored.
      </p>
    </Panel>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 px-4 py-2.5">
      <dt className="field-label w-40 shrink-0 pt-0.5">{label}</dt>
      <dd className="text-xs text-chalk-200">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settled
// ---------------------------------------------------------------------------

function SettledFooter({ report }: { report: ImportReport }) {
  return (
    <footer className="flex flex-wrap items-center gap-3 rounded-md border border-ink-700 bg-ink-850 px-4 py-3">
      <span className="min-w-0 flex-1 text-2xs text-chalk-600">
        {report.status === "COMMITTED"
          ? "This import is committed. State versions are append-only, so it cannot be undone from here."
          : "This import was discarded."}
      </span>
      {report.status === "COMMITTED" && report.production_id ? (
        <Link
          className="btn-go"
          href={`/?production=${encodeURIComponent(report.production_id)}`}
        >
          Open the production
        </Link>
      ) : null}
      <Link className="btn-quiet" href="/import">
        Import another
      </Link>
    </footer>
  );
}

function statusTone(report: ImportReport): "alert" | "clear" | "caution" | "neutral" {
  if (report.status === "COMMITTED") return "clear";
  if (report.status === "REJECTED" || report.status === "FAILED") return "alert";
  return report.can_commit ? "clear" : "alert";
}

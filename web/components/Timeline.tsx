"use client";

/**
 * The recovery timeline.
 *
 * This is the component the whole project turns on. When a candidate is
 * rejected, the row stays on screen, keeps its colour, and prints the rule code
 * with the observed and required values exactly as the API returned them —
 * because the next row says the planner generated a repair, and the two
 * together are the difference between this and a schedule chatbot.
 */

import type { Stage, StreamFrame } from "@/lib/types";

import { RuleViolationCard } from "./primitives";

const STAGE_LABEL: Record<Stage, string> = {
  EVENT_RECEIVED: "Disruption received",
  IMPACT_COMPUTED: "Impact computed",
  CANDIDATES_GENERATED: "Candidates generated",
  CANDIDATE_INVALID: "Candidate rejected",
  REPLANNING: "Replanning",
  CANDIDATE_VALID: "Repair validated",
  AWAITING_APPROVAL: "Awaiting approval",
  APPROVED: "Approved",
  EXECUTING: "Executing",
  VERIFIED: "Verified",
  FAILED: "Failed",
};

const STAGE_TONE: Record<Stage, string> = {
  EVENT_RECEIVED: "border-caution/40 bg-caution/5",
  IMPACT_COMPUTED: "border-board-500 bg-board-700",
  CANDIDATES_GENERATED: "border-board-500 bg-board-700",
  CANDIDATE_INVALID: "border-stamp-dim bg-stamp-wash",
  REPLANNING: "border-caution/40 bg-caution/5",
  CANDIDATE_VALID: "border-seal-dim bg-seal-wash",
  AWAITING_APPROVAL: "border-caution/40 bg-caution/5",
  APPROVED: "border-seal-dim bg-seal-wash",
  EXECUTING: "border-board-500 bg-board-700",
  VERIFIED: "border-seal-dim bg-seal-wash",
  FAILED: "border-stamp-dim bg-stamp-wash",
};

export function Timeline({ frames }: { frames: StreamFrame[] }) {
  if (frames.length === 0) {
    return (
      <div className="px-4 py-10 text-center text-xs text-chalk-600">
        Waiting for a disruption. Publish one with{" "}
        <code className="rounded bg-board-700 px-1.5 py-0.5 text-chalk-400">
          scripts/emit_disruption.py
        </code>
        , or press Run demo.
      </div>
    );
  }

  return (
    <ol className="space-y-2 p-3">
      {frames.map((frame, index) => (
        <TimelineRow key={`${frame.stage}-${frame.at}-${index}`} frame={frame} />
      ))}
    </ol>
  );
}

function TimelineRow({ frame }: { frame: StreamFrame }) {
  const stage = frame.stage;
  const isRejection = stage === "CANDIDATE_INVALID";

  return (
    <li
      className={`animate-slide-in rounded-md border px-3 py-2.5 ${STAGE_TONE[stage]} ${
        isRejection ? "animate-pulse-stamp" : ""
      }`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span
          className={`text-2xs font-semibold uppercase tracking-[0.12em] ${
            isRejection ? "text-stamp" : "text-chalk-400"
          }`}
        >
          {STAGE_LABEL[stage]}
        </span>
        <span className="tnum shrink-0 text-2xs text-chalk-600">{frame.at.slice(11, 19)}</span>
      </div>
      <StageDetail frame={frame} />
    </li>
  );
}

function StageDetail({ frame }: { frame: StreamFrame }) {
  switch (frame.stage) {
    case "EVENT_RECEIVED":
      return (
        <p className="mt-1 text-xs text-chalk-200">
          <span className="tnum">{String(frame.event_type ?? "")}</span>
          {frame.source ? <span className="text-chalk-600"> via {String(frame.source)}</span> : null}
        </p>
      );

    case "IMPACT_COMPUTED": {
      const direct = (frame.directly_affected_scene_ids as string[]) ?? [];
      const downstream = (frame.downstream_scene_ids as string[]) ?? [];
      const blast = typeof frame.blast_radius === "number" ? frame.blast_radius : 0;
      return (
        <p className="mt-1 text-xs text-chalk-200">
          <span className="tnum">{direct.length}</span> scene
          {direct.length === 1 ? "" : "s"} blocked ({direct.join(", ")})
          {downstream.length > 0 ? (
            <>
              , <span className="tnum">{downstream.length}</span> downstream (
              {downstream.join(", ")})
            </>
          ) : null}
          . Blast radius <span className="tnum text-caution">{(blast * 100).toFixed(0)}%</span>.
        </p>
      );
    }

    case "CANDIDATES_GENERATED":
      return (
        <p className="mt-1 text-xs text-chalk-200">
          <span className="tnum">{String(frame.count ?? "")}</span> candidates:{" "}
          <span className="tnum">{((frame.plan_ids as string[]) ?? []).join(", ")}</span>
        </p>
      );

    case "CANDIDATE_INVALID":
      return (
        <div className="mt-2">
          <RuleViolationCard
            code={String(frame.rule_code ?? "—")}
            message={String(
              frame.message ?? `Plan ${String(frame.label ?? "")} failed deterministic validation.`,
            )}
            observed={String(frame.observed ?? "—")}
            required={String(frame.required ?? "—")}
          />
        </div>
      );

    case "REPLANNING":
      return (
        <p className="mt-1 text-xs text-chalk-200">
          {String(frame.note ?? "Generating a repair for the rejected candidate.")}
        </p>
      );

    case "CANDIDATE_VALID":
      return (
        <p className="mt-1 text-xs text-seal">
          Plan <span className="tnum font-semibold">{String(frame.label ?? "")}</span> generated and
          valid.
        </p>
      );

    case "AWAITING_APPROVAL":
      return (
        <p className="mt-1 text-xs text-chalk-200">
          Recommending{" "}
          <span className="tnum text-caution">{String(frame.recommended_plan_id ?? "")}</span>.
          A producer has to approve it.
        </p>
      );

    case "APPROVED":
      return (
        <p className="mt-1 text-xs text-seal">
          <span className="tnum">{String(frame.plan_id ?? "")}</span> approved by{" "}
          {String(frame.approver ?? "")}.
        </p>
      );

    case "EXECUTING":
      return (
        <p className="mt-1 text-xs text-chalk-200">
          Running the seven-step transition for{" "}
          <span className="tnum">{String(frame.plan_id ?? "")}</span>.
        </p>
      );

    case "VERIFIED": {
      const checks = (frame.checks as string[]) ?? [];
      return (
        <p className="mt-1 text-xs text-seal">
          Committed as version <span className="tnum">{String(frame.new_version ?? "")}</span>.{" "}
          <span className="tnum">{checks.length}</span> verification checks green.
        </p>
      );
    }

    case "FAILED":
      return (
        <p className="mt-1 text-xs text-stamp">
          Stopped at <span className="tnum">{String(frame.step ?? "unknown step")}</span>:{" "}
          {String(frame.detail ?? "")}
        </p>
      );

    default:
      return null;
  }
}

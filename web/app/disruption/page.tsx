"use client";

/**
 * Live Disruption + Impact.
 *
 * The graph re-colouring the moment the event lands is the first thing in the
 * demo that proves the system is reacting rather than replaying. It listens to
 * the shared SSE stream and refetches the graph with the event id attached, so
 * nobody has to press anything.
 *
 * Two things this screen got wrong, both fixed here.
 *
 * It painted over itself. `h-full` on the grid pinned the page to the viewport,
 * so below the two-column breakpoint the three panels competed for one screen's
 * worth of height: the graph column resolved to 272px while the panel inside it
 * demanded 420px, and with every ancestor `overflow:visible` React Flow drew
 * 148px straight down the Event feed. The page could not be scrolled away from
 * it either, because the document was exactly the height of the window. The
 * grid now grows and the shell scrolls it, the second column engages at 1024px
 * rather than 1280px, and the graph panel has a bounded height that clips.
 *
 * And it was unreadable. Every scene became its own node in a single column, so
 * a 13-scene production laid out ~800px tall, `fitView` squeezed that into a
 * 380px box at roughly half scale, and 11px labels arrived at about 5px. The
 * fix is to draw less rather than to zoom harder: at rest each shooting day is
 * one node carrying its own scene count and hours, and scenes are only drawn
 * for the days a live disruption actually touches.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";

import { PRODUCTION_ID, api } from "@/lib/api";
import { lastFrame } from "@/lib/stream";
import type { GraphPayload, ImpactReport, NodeStatus, Schedule } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { Timeline } from "@/components/Timeline";
import { EmptyState, MetricCell, Panel } from "@/components/primitives";

/**
 * Graph node styling, through the theme's own variables.
 *
 * These were hardcoded hex from the old palette. React Flow takes inline
 * styles rather than classes, so the theme migration walked straight past them
 * and the graph stayed dark — which on the light theme was a panel of black
 * boxes on white paper, the one screen that looked broken.
 */
const STATUS_STYLE: Record<NodeStatus, { border: string; bg: string; text: string }> = {
  ok: {
    border: "rgb(var(--board-500))",
    bg: "rgb(var(--board-700))",
    text: "rgb(var(--chalk-400))",
  },
  impacted: {
    border: "rgb(var(--stamp))",
    bg: "rgb(var(--stamp) / 0.14)",
    text: "rgb(var(--stamp))",
  },
  downstream: {
    border: "rgb(var(--caution))",
    bg: "rgb(var(--caution) / 0.14)",
    text: "rgb(var(--caution))",
  },
};

/** What each colour means, in the words the narration uses. */
const LEGEND: { status: NodeStatus; label: string }[] = [
  { status: "ok", label: "on track" },
  { status: "impacted", label: "blocked" },
  { status: "downstream", label: "downstream" },
];

export default function DisruptionPage() {
  const { frames } = useShell();
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [impact, setImpact] = useState<ImpactReport | null>(null);

  const impactFrame = lastFrame(frames, "IMPACT_COMPUTED");
  const eventFrame = lastFrame(frames, "EVENT_RECEIVED");
  const eventId = eventFrame ? String(eventFrame.event_id ?? "") : "";

  const loadGraph = useCallback((withEvent: string) => {
    api
      .graph(PRODUCTION_ID, withEvent || undefined)
      .then(setGraph)
      .catch(() => setGraph(null));
  }, []);

  useEffect(() => {
    loadGraph(eventId);
  }, [eventId, loadGraph]);

  // The board, for the hours and scene counts a collapsed day node prints. The
  // graph payload carries neither, and a day node that invented them would be
  // the one number on screen nothing computed.
  useEffect(() => {
    api
      .schedule(PRODUCTION_ID)
      .then(setSchedule)
      .catch(() => setSchedule(null));
  }, []);

  useEffect(() => {
    if (impactFrame === null) return;
    setImpact({
      event_id: String(impactFrame.event_id ?? eventId),
      directly_affected_scene_ids: (impactFrame.directly_affected_scene_ids as string[]) ?? [],
      downstream_scene_ids: (impactFrame.downstream_scene_ids as string[]) ?? [],
      affected_cast_ids: (impactFrame.affected_cast_ids as string[]) ?? [],
      affected_crew_ids: [],
      affected_location_ids: [],
      affected_equipment_ids: [],
      affected_days: (impactFrame.affected_days as string[]) ?? [],
      downstream_dependency_count: Number(impactFrame.downstream_dependency_count ?? 0),
      blast_radius: Number(impactFrame.blast_radius ?? 0),
    });
  }, [impactFrame, eventId]);

  const { nodes, edges } = useMemo(
    () => toFlow(graph, schedule, eventId ? impact : null),
    [graph, schedule, impact, eventId],
  );

  return (
    // `min-h-full`, not `h-full`: the rows size themselves and the shell's own
    // `overflow-y-auto` scrolls whatever that comes to. Pinning the page to the
    // viewport is what made the panels overlap instead of stack.
    <div className="grid min-h-full grid-cols-1 gap-4 p-6 lg:grid-cols-[1fr_380px]">
      <div className="flex min-h-0 flex-col gap-4">
        <Panel title="Impact">
          {impact === null ? (
            <EmptyState>
              {/* Two different states, and saying the wrong one is worse than
                  saying nothing: the event feed to the right can be showing a
                  disruption while this panel still has no impact report,
                  because impact is computed during recovery rather than on
                  ingest. Claiming "no live disruption" next to a feed that says
                  one arrived reads as a broken screen. */}
              {eventId
                ? "Disruption received. The impact appears here once recovery computes it."
                : "No live disruption. The graph below shows the production at rest."}
            </EmptyState>
          ) : (
            <div className="grid grid-cols-2 gap-6 px-4 py-4 sm:grid-cols-5">
              <MetricCell
                label="scenes blocked"
                value={impact.directly_affected_scene_ids.length}
                tone="alert"
                hint={impact.directly_affected_scene_ids.join(", ")}
              />
              <MetricCell
                label="downstream"
                value={impact.downstream_scene_ids.length}
                hint={impact.downstream_scene_ids.join(", ") || "none"}
              />
              <MetricCell label="cast affected" value={impact.affected_cast_ids.length} />
              <MetricCell label="days" value={impact.affected_days.length} />
              <MetricCell
                label="blast radius"
                value={`${(impact.blast_radius * 100).toFixed(0)}%`}
                tone="alert"
                hint="of remaining scenes"
              />
            </div>
          )}
        </Panel>

        {/* A bounded, deterministic height, and clipped whatever happens inside
            it. The old `min-h-[420px] flex-1` is what let React Flow escape. */}
        <Panel
          title="Dependency graph"
          className="flex h-[clamp(320px,48vh,560px)] flex-col overflow-hidden"
        >
          <GraphLegend expanded={eventId !== ""} />
          {/* Flex rather than `calc(100% - 2.6rem)`: that number was the panel
              header guessed at, it was two pixels short, and it knew nothing
              about the legend strip. The canvas takes what is left. */}
          <div className="min-h-0 w-full flex-1 overflow-hidden">
            {graph === null ? (
              <EmptyState>Graph unavailable.</EmptyState>
            ) : (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                // Never shrink past the point where a label stops being a
                // label, and never magnify a small graph into a diagram of
                // four enormous boxes.
                minZoom={0.45}
                fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
                proOptions={{ hideAttribution: true }}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
              >
                <Background color="rgb(var(--board-600))" gap={18} />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
        </Panel>
      </div>

      <Panel title="Event feed" className="flex min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Timeline frames={[...frames].reverse()} />
        </div>
      </Panel>
    </div>
  );
}

/**
 * Three swatches, in the graph's own colours.
 *
 * The recolour is the moment the demo turns on, and until now nothing on screen
 * said what the new colour meant. Drawn from `STATUS_STYLE` rather than fresh
 * hex, so a legend can never disagree with the thing it describes.
 */
function GraphLegend({ expanded }: { expanded: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-4 border-b border-board-600 px-4 py-1.5">
      {LEGEND.map(({ status, label }) => (
        <span key={status} className="flex items-center gap-1.5 text-2xs text-chalk-600">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{
              background: STATUS_STYLE[status].bg,
              border: `1px solid ${STATUS_STYLE[status].border}`,
            }}
          />
          {label}
        </span>
      ))}
      <span className="ml-auto text-2xs text-chalk-600">
        {expanded ? "affected days expanded to scenes" : "one node per shooting day"}
      </span>
    </div>
  );
}

/** `day:2026-09-10` and `scene:S17` — the id carries the type and the key. */
const dayKey = (id: string) => id.replace(/^day:/, "");

/** "Wed 9 Sept", in the reader's locale, from an ISO date. */
function formatDay(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

/**
 * Lay the graph out in columns by node type.
 *
 * A force layout looks livelier and tells you less: what a producer needs to
 * see is which scenes are hit and what they hang off, and columns make that
 * readable at a glance.
 *
 * Scenes are drawn only for days a live disruption touches — the affected days
 * and any day holding a downstream scene. Every other day stays a single node
 * printing what it contains, which is the difference between a graph you can
 * read and a grey wash. Cast, locations and equipment are all still drawn; they
 * are split into their own columns rather than stacked into one, because the
 * tallest column is what `fitView` scales against and a 13-deep stack is what
 * was costing the labels their legibility.
 */
function toFlow(
  graph: GraphPayload | null,
  schedule: Schedule | null,
  impact: ImpactReport | null,
): { nodes: Node[]; edges: Edge[] } {
  if (graph === null) return { nodes: [], edges: [] };

  const scenesByDay = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.kind !== "SCHEDULED_ON") continue;
    const day = dayKey(edge.source);
    scenesByDay.set(day, [...(scenesByDay.get(day) ?? []), edge.target]);
  }

  // Which days get opened up. Nothing at rest; on a live event, the days the
  // impact names plus any day carrying a knock-on scene.
  const expanded = new Set<string>();
  if (impact !== null) {
    for (const day of impact.affected_days) expanded.add(day);
    const downstream = new Set(impact.downstream_scene_ids.map((id) => `scene:${id}`));
    for (const [day, sceneIds] of scenesByDay) {
      if (sceneIds.some((id) => downstream.has(id))) expanded.add(day);
    }
  }

  const dayByDate = new Map(schedule?.days.map((day) => [day.date, day]) ?? []);
  const shownScenes = new Set<string>();
  for (const day of expanded) {
    for (const sceneId of scenesByDay.get(day) ?? []) shownScenes.add(sceneId);
  }

  const nodes: Node[] = [];
  // The canvas is wide and short, so the layout has to be too: `fitView`
  // scales against the tallest column, and a nine-deep stack of days was what
  // dragged every label down to about five pixels on screen. A type that would
  // run deeper than this wraps into another column of its own.
  const MAX_ROWS = 5;
  const cursor = { column: 0, row: 0, type: "" };

  const place = (id: string, type: string, label: string, status: NodeStatus) => {
    if (type !== cursor.type || cursor.row >= MAX_ROWS) {
      if (cursor.type !== "") cursor.column += 1;
      cursor.row = 0;
      cursor.type = type;
    }
    const { column, row } = cursor;
    cursor.row += 1;
    const style = STATUS_STYLE[status];
    nodes.push({
      id,
      position: { x: column * 165, y: row * 50 },
      data: { label },
      draggable: false,
      style: {
        background: style.bg,
        // A thicker edge on anything the disruption touched, so status reads
        // before the label does.
        border: `${status === "ok" ? 1 : 2}px solid ${style.border}`,
        borderRadius: 6,
        color: style.text,
        fontSize: 12,
        fontFamily: "ui-monospace, monospace",
        padding: "6px 10px",
        width: 150,
      },
    });
  };

  for (const node of graph.nodes) {
    if (node.type === "shooting_day") {
      const date = dayKey(node.id);
      if (expanded.has(date)) {
        place(node.id, node.type, formatDay(date), node.status);
        continue;
      }
      // Collapsed: one node standing for the whole day, printing what it
      // holds. The count and the hours come off the board, not from here.
      const board = dayByDate.get(date);
      const count = board?.scene_count ?? (scenesByDay.get(date) ?? []).length;
      const hours = board ? dayHours(board.call_time, board.wrap_time) : null;
      const parts = [formatDay(date), `${count} ${count === 1 ? "scene" : "scenes"}`];
      if (hours !== null) parts.push(`${hours.toFixed(1)}h`);
      place(node.id, node.type, parts.join(" · "), node.status);
      continue;
    }

    if (node.type === "scene") {
      if (!shownScenes.has(node.id)) continue;
      place(node.id, node.type, node.label, node.status);
      continue;
    }

    place(node.id, node.type, node.label, node.status);
  }

  // An edge to a node that was collapsed away would render as a line into
  // nothing, so only edges with both ends on screen survive.
  const present = new Set(nodes.map((node) => node.id));
  const edges: Edge[] = graph.edges
    .filter((edge) => present.has(edge.source) && present.has(edge.target))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      style: { stroke: "rgb(var(--board-500))", strokeWidth: 1 },
      animated: edge.kind === "PREREQUISITE",
    }));

  return { nodes, edges };
}

/** Shoot length in hours, from the day's own call and wrap. */
function dayHours(call: string, wrap: string): number | null {
  const from = new Date(call).getTime();
  const to = new Date(wrap).getTime();
  if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return null;
  return (to - from) / 3_600_000;
}

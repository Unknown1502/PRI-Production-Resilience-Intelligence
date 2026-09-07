"use client";

/**
 * Live Disruption + Impact.
 *
 * The graph re-colouring the moment the event lands is the first thing in the
 * demo that proves the system is reacting rather than replaying. It listens to
 * the shared SSE stream and refetches the graph with the event id attached, so
 * nobody has to press anything.
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
import type { GraphPayload, ImpactReport, NodeStatus } from "@/lib/types";

import { useShell } from "@/components/Shell";
import { Timeline } from "@/components/Timeline";
import { EmptyState, MetricCell, Panel } from "@/components/primitives";

const STATUS_STYLE: Record<NodeStatus, { border: string; bg: string; text: string }> = {
  ok: { border: "#252b38", bg: "#12151c", text: "#9aa3b4" },
  impacted: { border: "#ff6b4a", bg: "#2a1310", text: "#ff6b4a" },
  downstream: { border: "#f5c451", bg: "#241f10", text: "#f5c451" },
};

export default function DisruptionPage() {
  const { frames } = useShell();
  const [graph, setGraph] = useState<GraphPayload | null>(null);
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

  const { nodes, edges } = useMemo(() => toFlow(graph), [graph]);

  return (
    <div className="grid h-full grid-cols-1 gap-4 p-6 xl:grid-cols-[1fr_380px]">
      <div className="flex min-h-0 flex-col gap-4">
        <Panel title="Impact">
          {impact === null ? (
            <EmptyState>
              No live disruption. The graph below shows the production at rest.
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

        <Panel title="Dependency graph" className="min-h-[420px] flex-1">
          <div className="h-[calc(100%-2.6rem)] min-h-[380px] w-full">
            {graph === null ? (
              <EmptyState>Graph unavailable.</EmptyState>
            ) : (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                fitView
                proOptions={{ hideAttribution: true }}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
              >
                <Background color="#1a1e28" gap={18} />
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
 * Lay the graph out in columns by node type.
 *
 * A force layout looks livelier and tells you less: what a producer needs to
 * see is which scenes are hit and what they hang off, and columns make that
 * readable at a glance.
 */
function toFlow(graph: GraphPayload | null): { nodes: Node[]; edges: Edge[] } {
  if (graph === null) return { nodes: [], edges: [] };

  const columns: Record<string, number> = {
    shooting_day: 0,
    scene: 1,
    person: 2,
    location: 2,
    equipment: 2,
  };
  const counters: Record<string, number> = {};

  const nodes: Node[] = graph.nodes.map((node) => {
    const column = columns[node.type] ?? 3;
    const row = counters[node.type] ?? 0;
    counters[node.type] = row + 1;
    const style = STATUS_STYLE[node.status];

    return {
      id: node.id,
      position: { x: column * 260 + (node.type === "location" ? 0 : 0), y: row * 62 },
      data: { label: node.label },
      draggable: false,
      style: {
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: 6,
        color: style.text,
        fontSize: 11,
        fontFamily: "ui-monospace, monospace",
        padding: "6px 10px",
        width: 200,
      },
    };
  });

  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    style: { stroke: "#252b38", strokeWidth: 1 },
    animated: edge.kind === "PREREQUISITE",
  }));

  return { nodes, edges };
}

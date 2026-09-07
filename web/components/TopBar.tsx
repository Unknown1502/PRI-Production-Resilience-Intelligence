"use client";

/**
 * The persistent header.
 *
 * The state version sits here in large monospaced digits because watching it
 * tick from v1 to v2 after execution is the clearest single signal that
 * something real happened — and it is on screen for the whole demo, not just
 * the verification page.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ConnectionState } from "@/lib/stream";

import { StatusPill } from "./primitives";

const CONNECTION_TONE: Record<ConnectionState, "clear" | "caution" | "alert" | "neutral"> = {
  live: "clear",
  connecting: "caution",
  reconnecting: "caution",
  closed: "alert",
};

export function TopBar({
  title,
  version,
  connection,
  agentMode,
  onReset,
}: {
  title: string;
  version: number | null;
  connection: ConnectionState;
  agentMode?: "agent" | "deterministic" | null;
  onReset?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [sha, setSha] = useState<string | null>(null);

  useEffect(() => {
    api
      .health()
      .then((health) => setSha(health.git_sha.slice(0, 7)))
      .catch(() => setSha(null));
  }, []);

  const reset = useCallback(async () => {
    setBusy(true);
    try {
      await api.resetDemo();
      onReset?.();
    } finally {
      setBusy(false);
    }
  }, [onReset]);

  return (
    <header className="flex h-14 shrink-0 items-center gap-5 border-b border-board-600 bg-board-800 px-5">
      <div className="min-w-0">
        <h1 className="truncate text-sm font-semibold text-chalk-100">{title}</h1>
        <p className="text-2xs text-chalk-600">Production Resilience Intelligence</p>
      </div>

      <div className="ml-2 flex items-baseline gap-2 rounded-md border border-board-500 bg-board-700 px-3 py-1.5">
        <span className="field-label">state</span>
        <span className="tnum text-xl font-bold leading-none text-seal">
          v{version ?? "—"}
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        {agentMode === "deterministic" ? (
          <StatusPill tone="caution">deterministic mode</StatusPill>
        ) : agentMode === "agent" ? (
          <StatusPill tone="clear">gemini</StatusPill>
        ) : null}

        {sha ? <span className="tnum text-2xs text-chalk-600">{sha}</span> : null}

        <StatusPill tone={CONNECTION_TONE[connection]}>
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              connection === "live" ? "bg-seal" : "bg-current"
            }`}
          />
          {connection}
        </StatusPill>

        {onReset ? (
          <button type="button" className="btn-quiet" onClick={reset} disabled={busy}>
            {busy ? "Resetting…" : "Reset demo"}
          </button>
        ) : null}
      </div>
    </header>
  );
}

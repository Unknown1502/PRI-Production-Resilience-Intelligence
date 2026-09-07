"use client";

/**
 * The application shell: left nav, top bar, and the shared stream.
 *
 * The SSE connection lives here rather than per-page so navigating between
 * Disruption and Recovery mid-demo does not drop and re-establish it — a
 * reconnect would lose the frames that arrived while the page was swapping.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { PRODUCTION_ID, api } from "@/lib/api";
import { useRecoveryStream } from "@/lib/stream";
import type { ConnectionState } from "@/lib/stream";
import type { RecoveryMode, StreamFrame } from "@/lib/types";

import { TopBar } from "./TopBar";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/import", label: "Import a production" },
  { href: "/disruption", label: "Disruption" },
  { href: "/recovery", label: "Recovery" },
  { href: "/governance", label: "Governance" },
  { href: "/verification", label: "Verification" },
  { href: "/audit", label: "Audit" },
] as const;

interface ShellState {
  /**
   * The production every screen is looking at.
   *
   * `?production=` overrides the default, which is how a freshly imported
   * production becomes viewable without a redeploy.
   */
  productionId: string;
  frames: StreamFrame[];
  connection: ConnectionState;
  version: number | null;
  title: string;
  mode: RecoveryMode | null;
  setMode: (mode: RecoveryMode | null) => void;
  refreshVersion: () => void;
}

const ShellContext = createContext<ShellState | null>(null);

/** Read the shell's shared stream and version. */
export function useShell(): ShellState {
  const context = useContext(ShellContext);
  if (context === null) {
    throw new Error("useShell must be called inside <Shell>");
  }
  return context;
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const productionId = useSearchParams().get("production") ?? PRODUCTION_ID;
  const { frames, connection } = useRecoveryStream(productionId);
  const [version, setVersion] = useState<number | null>(null);
  const [title, setTitle] = useState("Loading…");
  const [mode, setMode] = useState<RecoveryMode | null>(null);

  const refreshVersion = useCallback(() => {
    api
      .schedule(productionId)
      .then((schedule) => {
        setVersion(schedule.version);
        setTitle(schedule.title);
      })
      .catch(() => {
        // A cold API means the overview renders its cached snapshot; the top
        // bar showing "v—" is more honest than a spinner that never resolves.
        setTitle(productionId === PRODUCTION_ID ? "Night Train to Kochi" : productionId);
      });
  }, [productionId]);

  useEffect(refreshVersion, [refreshVersion]);

  // The version changes exactly when a transition verifies, so watching the
  // stream is cheaper and faster than polling for it.
  useEffect(() => {
    const last = frames[frames.length - 1];
    if (last?.stage === "VERIFIED" || last?.stage === "EVENT_RECEIVED") {
      refreshVersion();
    }
  }, [frames, refreshVersion]);

  return (
    <ShellContext.Provider
      value={{ productionId, frames, connection, version, title, mode, setMode, refreshVersion }}
    >
      <div className="flex h-dvh flex-col">
        <TopBar
          title={title}
          version={version}
          connection={connection}
          agentMode={mode}
          onReset={refreshVersion}
        />
        <div className="flex min-h-0 flex-1">
          <nav className="w-44 shrink-0 border-r border-board-600 bg-board-800 py-3">
            <ul className="space-y-0.5 px-2">
              {NAV.map((item) => {
                const active =
                  item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={`block rounded-md px-3 py-2 text-xs transition-colors ${
                        active
                          ? "bg-board-600 font-medium text-chalk-100"
                          : "text-chalk-400 hover:bg-board-700 hover:text-chalk-200"
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </ShellContext.Provider>
  );
}

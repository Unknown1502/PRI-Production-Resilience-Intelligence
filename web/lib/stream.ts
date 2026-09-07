"use client";

/**
 * EventSource hook for the recovery timeline.
 *
 * Reconnects on its own with a backoff, because a Cloud Run instance recycling
 * mid-demo must not leave the screens frozen with nobody noticing. The
 * connection state is surfaced so the top bar can show a live indicator that
 * tells the truth.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { STAGES, type Stage, type StreamFrame } from "./types";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

interface UseStreamResult {
  frames: StreamFrame[];
  latest: StreamFrame | null;
  connection: ConnectionState;
  clear: () => void;
}

/** Longest gap between reconnect attempts. */
const MAX_BACKOFF_MS = 10_000;
/** Beyond this the oldest frames are dropped; the timeline never needs more. */
const MAX_FRAMES = 200;

export function useRecoveryStream(productionId?: string): UseStreamResult {
  const [frames, setFrames] = useState<StreamFrame[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const sourceRef = useRef<EventSource | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => setFrames([]), []);

  useEffect(() => {
    let cancelled = false;

    const push = (frame: StreamFrame) => {
      setFrames((existing) => {
        const next = [...existing, frame];
        return next.length > MAX_FRAMES ? next.slice(next.length - MAX_FRAMES) : next;
      });
    };

    const connect = () => {
      if (cancelled) return;

      const source = new EventSource(api.streamUrl(productionId));
      sourceRef.current = source;

      source.onopen = () => {
        attemptRef.current = 0;
        setConnection("live");
      };

      // Each stage is a named SSE event, so the server can add stages without
      // the client having to re-parse a discriminated blob.
      for (const stage of STAGES) {
        source.addEventListener(stage, (event) => {
          try {
            const data = JSON.parse((event as MessageEvent<string>).data) as StreamFrame;
            push({ ...data, stage: stage as Stage });
          } catch {
            // A malformed frame is a bug on the server, not something the
            // producer watching this screen can act on. Skip it.
          }
        });
      }

      source.onerror = () => {
        source.close();
        sourceRef.current = null;
        if (cancelled) return;

        setConnection("reconnecting");
        attemptRef.current += 1;
        const backoff = Math.min(500 * 2 ** (attemptRef.current - 1), MAX_BACKOFF_MS);
        timerRef.current = setTimeout(connect, backoff);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      sourceRef.current?.close();
      sourceRef.current = null;
      setConnection("closed");
    };
  }, [productionId]);

  return {
    frames,
    latest: frames.length > 0 ? (frames[frames.length - 1] ?? null) : null,
    connection,
    clear,
  };
}

/** Return the most recent frame for a stage, or null. */
export function lastFrame(frames: StreamFrame[], stage: Stage): StreamFrame | null {
  for (let i = frames.length - 1; i >= 0; i -= 1) {
    const frame = frames[i];
    if (frame && frame.stage === stage) return frame;
  }
  return null;
}

"use client";

/**
 * Report a disruption in your own words.
 *
 * The scripted button next to this runs the same disruption every time, which
 * is what makes the video reproducible. This is the other half: a judge types
 * a sentence and watches their own words go through the pipeline.
 *
 * Two things are deliberately visible on screen rather than hidden:
 *
 *   What exists. The panel lists the locations and cast in the loaded
 *   production, because the system will refuse anything else and a refusal is
 *   only useful if you can see what the alternatives were.
 *
 *   The refusal itself. When PRI cannot tie a sentence to something real it
 *   says so and offers the nearest matches. That is the interesting outcome,
 *   not an error state to bury — it is the demonstration that the model is
 *   selecting from the production rather than inventing entries in it.
 *
 * The passcode is typed by the presenter and kept in this browser only. It is
 * not proxied in like the API key: this endpoint spends a model call per
 * request on a public URL, so it is gated on the person driving, not on the
 * page being open.
 */

import { useCallback, useEffect, useState } from "react";

import { API_URL, PRODUCTION_ID } from "@/lib/api";

const PASSCODE_KEY = "pri-inject-passcode";

type Suggestion = { id: string; label: string; kind: string };

type InjectResult = {
  status: "accepted" | "needs_clarification";
  detail: string;
  entity_label?: string | null;
  day?: string | null;
  suggestions?: Suggestion[];
};

type Injectable = {
  locations: { id: string; label: string }[];
  cast: { id: string; label: string }[];
  enabled: boolean;
};

export function InjectDisruption({ productionId = PRODUCTION_ID }: { productionId?: string }) {
  const [passcode, setPasscode] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InjectResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [what, setWhat] = useState<Injectable | null>(null);

  useEffect(() => {
    try {
      setPasscode(localStorage.getItem(PASSCODE_KEY) ?? "");
    } catch {
      // A browser with site data blocked still works; the presenter retypes it.
    }
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/disruptions/injectable?production_id=${productionId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setWhat)
      .catch(() => setWhat(null));
  }, [productionId]);

  const reset = useCallback(async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch(`${API_URL}/api/demo/reset`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      if (!response.ok) {
        setError("Could not reset the demo.");
      } else {
        const body = await response.json();
        setResult({
          status: "accepted",
          detail: `Back to version ${body.version}. ${body.scenes} scenes, ${body.days} days.`,
        });
      }
    } catch {
      setError("Could not reach the console.");
    } finally {
      setBusy(false);
    }
  }, []);

  const send = useCallback(async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      localStorage.setItem(PASSCODE_KEY, passcode);
    } catch {
      // Not being able to remember it is not a reason to refuse to send it.
    }
    try {
      const response = await fetch(`${API_URL}/api/disruptions/inject`, {
        method: "POST",
        headers: { "content-type": "application/json", "X-Inject-Passcode": passcode },
        body: JSON.stringify({ text, production_id: productionId }),
      });
      if (response.status === 404) {
        setError("Live injection is switched off on this deployment.");
      } else if (response.status === 401) {
        setError("That passcode was not accepted.");
      } else if (response.status === 429) {
        setError("Too many in a minute. Give it a moment.");
      } else if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.detail ?? "Could not send that.");
      } else {
        const body: InjectResult = await response.json();
        setResult(body);
        if (body.status === "accepted") setText("");
      }
    } catch {
      setError("Could not reach the console.");
    } finally {
      setBusy(false);
    }
  }, [passcode, productionId, text]);

  if (what && !what.enabled) return null;

  return (
    <section className="flex flex-col gap-3 rounded border border-board-600 p-4">
      <div>
        <h3 className="text-sm font-semibold">Report it in your own words</h3>
        <p className="text-2xs text-chalk-600">
          Typed reports run the same pipeline as any other event: published to Kafka, consumed,
          validated, replanned. PRI will refuse anything it cannot find in this production.
        </p>
      </div>

      <input
        type="password"
        value={passcode}
        onChange={(e) => setPasscode(e.target.value)}
        placeholder="Injection passcode"
        aria-label="Injection passcode"
        className="rounded border border-board-600 bg-transparent px-3 py-2 text-xs"
      />

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        maxLength={500}
        placeholder="The courtyard permit fell through for Thursday"
        aria-label="What happened"
        className="rounded border border-board-600 bg-transparent px-3 py-2 text-xs"
      />

      <div className="flex items-center gap-2">
        <button type="button" className="btn-go" onClick={send} disabled={busy || !text.trim()}>
          {busy ? "Reading it…" : "Send it through"}
        </button>
        {/* Between judges: put the board back to version 1 so the next person
            sees the same starting state, without a redeploy. */}
        <button
          type="button"
          onClick={reset}
          disabled={busy}
          className="rounded border border-board-600 px-3 py-2 text-2xs text-chalk-600"
        >
          Reset to base scenario
        </button>
      </div>

      {error ? (
        <p className="text-2xs text-stamp" role="alert">
          {error}
        </p>
      ) : null}

      {result ? (
        <div
          role="status"
          className={`rounded border p-3 text-2xs ${
            result.status === "accepted" ? "border-board-600" : "border-caution"
          }`}
        >
          <p>{result.detail}</p>
          {result.suggestions?.length ? (
            <p className="mt-2 text-chalk-600">
              Did you mean:{" "}
              {result.suggestions.map((s) => s.label).join(", ")}?
            </p>
          ) : null}
        </div>
      ) : null}

      {what ? (
        <details className="text-2xs text-chalk-600">
          <summary className="cursor-pointer">What is in this production</summary>
          <p className="mt-2">
            <strong>Locations:</strong> {what.locations.map((l) => l.label).join(", ")}
          </p>
          <p>
            <strong>Cast:</strong> {what.cast.map((c) => c.label).join(", ")}
          </p>
        </details>
      ) : null}
    </section>
  );
}

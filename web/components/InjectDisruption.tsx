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
  event_type?: string | null;
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
  // Whether localStorage has been consulted yet. Without it the
  // presenter-access fold flashes open on every load before the saved
  // passcode arrives.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      setPasscode(localStorage.getItem(PASSCODE_KEY) ?? "");
    } catch {
      // A browser with site data blocked still works; the presenter retypes it.
    }
    setReady(true);
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

  const refused = result?.status === "needs_clarification";
  const accepted = result?.status === "accepted";

  return (
    <section className="flex flex-col gap-3 rounded border border-board-600 p-4">
      <div>
        <h3 className="text-sm font-semibold">Report it in your own words</h3>
        <p className="text-2xs text-chalk-600">
          Typed reports run the same pipeline as any other event: published to Kafka, consumed,
          validated, replanned. PRI will refuse anything it cannot find in this production.
        </p>
      </div>

      {/* What exists, on screen before anything is typed.
          The refusal below only lands if the alternatives were already visible
          — otherwise "it is not in this production" is a claim the viewer has
          to take on trust. This was behind a <details> nobody opens. */}
      <ClosedSet what={what} />

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        maxLength={500}
        placeholder="The courtyard permit fell through for Thursday"
        aria-label="What happened"
        className="rounded border border-board-600 bg-transparent px-3 py-2 text-xs"
      />

      {/* The passcode is a presenter's tool, not the first thing a judge
          browsing a public URL should meet. Folded away, and left folded when
          the browser already has one. */}
      <details open={passcode === "" && ready}>
        <summary className="cursor-pointer text-2xs text-chalk-600">Presenter access</summary>
        <input
          type="password"
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          placeholder="Injection passcode"
          aria-label="Injection passcode"
          className="mt-2 w-full rounded border border-board-600 bg-transparent px-3 py-2 text-xs"
        />
      </details>

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

      {refused && result ? (
        <Refusal result={result} onPick={(label) => setText(label)} />
      ) : null}

      {accepted && result ? <Accepted result={result} /> : null}
    </section>
  );
}

/**
 * The refusal, given the weight it deserves.
 *
 * This is the single most convincing thing the console does, and it was
 * rendered as a small grey box that read like a validation error. An agent
 * that declines to act on input it cannot ground is the whole argument
 * against "the model made the plan up", so it gets a card, the alternatives
 * as something you can click, and a sentence saying what just happened.
 */
function Refusal({
  result,
  onPick,
}: {
  result: InjectResult;
  onPick: (label: string) => void;
}) {
  return (
    <div className="rounded border border-caution bg-caution/10 p-4" role="status">
      <h4 className="text-xs font-semibold text-caution">
        PRI declined this — it is not in this production
      </h4>
      <p className="mt-1.5 text-2xs leading-relaxed text-chalk-200">{result.detail}</p>

      {result.suggestions?.length ? (
        <div className="mt-3">
          <p className="field-label">did you mean</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {result.suggestions.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => onPick(s.label)}
                className="rounded-sm border border-board-500 bg-board-700 px-2 py-1 text-2xs
                           text-chalk-200 hover:border-caution"
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <p className="mt-3 border-t border-caution/30 pt-2 text-2xs leading-relaxed text-chalk-600">
        The planner selects from the loaded production. It does not invent entities.
      </p>
    </div>
  );
}

/**
 * What was actually understood, not merely that something was.
 *
 * "Accepted" on its own asks the viewer to trust that the right thing was
 * extracted from their sentence. Printing the entity and the day back to them
 * is what makes the classification checkable.
 */
function Accepted({ result }: { result: InjectResult }) {
  return (
    <div className="rounded border border-seal-dim bg-seal-wash p-4" role="status">
      <h4 className="text-xs font-semibold text-seal">Accepted and published</h4>
      <dl className="mt-2.5 flex flex-wrap gap-6">
        <div>
          <dt className="field-label">read as</dt>
          <dd className="text-xs text-chalk-100">{result.entity_label ?? "—"}</dd>
        </div>
        <div>
          <dt className="field-label">on</dt>
          <dd className="tnum text-xs text-chalk-100">{result.day ?? "—"}</dd>
        </div>
        <div>
          <dt className="field-label">as</dt>
          <dd className="tnum text-xs text-chalk-100">{result.event_type ?? "—"}</dd>
        </div>
      </dl>
      <p className="mt-3 text-2xs leading-relaxed text-chalk-600">
        On <span className="tnum">production.events</span>. The consumer picks it up and the
        timeline narrates the rest.
      </p>
    </div>
  );
}

/** The locations and cast a report may name, two lines, expandable. */
function ClosedSet({ what }: { what: Injectable | null }) {
  const [open, setOpen] = useState(false);
  if (what === null) return null;

  const line = (items: { id: string; label: string }[]) => {
    const shown = open ? items : items.slice(0, 3);
    const rest = items.length - shown.length;
    return (
      <>
        {shown.map((item) => item.label).join(", ")}
        {rest > 0 ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="ml-1 text-caution underline decoration-dotted"
          >
            +{rest} more
          </button>
        ) : null}
      </>
    );
  };

  return (
    <div className="rounded-sm border border-board-600 bg-board-700 px-3 py-2 text-2xs leading-relaxed text-chalk-600">
      <p>
        <span className="field-label">locations</span>{" "}
        <span className="text-chalk-400">{line(what.locations)}</span>
      </p>
      <p className="mt-1">
        <span className="field-label">cast</span>{" "}
        <span className="text-chalk-400">{line(what.cast)}</span>
      </p>
    </div>
  );
}

"use client";

/**
 * Import a production — the first screen a stranger's production reaches.
 *
 * Everything on it exists to make the next click obvious: drop a board, or if
 * you have nothing to drop, take the template or a sample. The seven-sheet
 * contract is rendered from `/api/import/spec/sheets` rather than restated in
 * TypeScript, so a column added in `spec.py` shows up here with no frontend
 * change — the one place a column is defined stays the one place.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ImportSampleInfo, ImportSpec } from "@/lib/types";

import { DropZone } from "@/components/DropZone";
import { EmptyState, Panel, StatusPill } from "@/components/primitives";

/**
 * Used only until `/api/import/spec/sheets` answers, so the drop zone is live
 * on first paint instead of a spinner. The server re-checks both, and its
 * answer replaces these the moment it arrives.
 */
const FALLBACK_MAX_BYTES = 10 * 1024 * 1024;
const FALLBACK_ACCEPT = ".xlsx,.xlsm,.zip,.csv";

export default function ImportPage() {
  const router = useRouter();
  const [spec, setSpec] = useState<ImportSpec | null>(null);
  const [samples, setSamples] = useState<ImportSampleInfo[]>([]);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let live = true;
    Promise.all([api.importSpec(), api.importSamples()])
      .then(([nextSpec, sampleList]) => {
        if (!live) return;
        setSpec(nextSpec);
        setSamples(sampleList.samples);
        setOffline(false);
      })
      .catch(() => live && setOffline(true));
    return () => {
      live = false;
    };
  }, []);

  const onUploaded = useCallback(
    (stagingId: string) => router.push(`/import/${stagingId}`),
    [router],
  );

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <header className="space-y-1">
        <h1 className="text-base font-semibold text-chalk-100">Import a production</h1>
        <p className="text-xs leading-relaxed text-chalk-400">
          Upload the 1st AD&apos;s board. PRI reads it, checks it, and shows you what it
          found. Nothing is written to the production until you confirm on the next
          screen — an import that looks wrong can simply be discarded.
        </p>
      </header>

      {offline ? (
        <Panel>
          <EmptyState>
            The API is not answering. The upload below will tell you the same thing if you
            try it — start the backend and reload.
          </EmptyState>
        </Panel>
      ) : null}

      <DropZone
        accept={spec ? spec.accepted_extensions.join(",") : FALLBACK_ACCEPT}
        maxBytes={spec?.max_upload_bytes ?? FALLBACK_MAX_BYTES}
        onUploaded={onUploaded}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Nothing to upload yet">
          <div className="space-y-3 px-4 py-4">
            <p className="text-xs leading-relaxed text-chalk-400">
              The template is the same seven sheets PRI reads, with two example rows that
              reference each other correctly. Delete the examples, paste your board in,
              upload it.
            </p>
            <a
              className="btn-go"
              href={api.importTemplateUrl()}
              download
              data-testid="download-template"
            >
              Download the template
            </a>
            {spec ? (
              <p className="text-2xs text-chalk-600">
                Template version {spec.template_version} · up to{" "}
                {Math.round(spec.max_upload_bytes / (1024 * 1024))} MB ·{" "}
                {spec.accepted_extensions.join(" · ")}
              </p>
            ) : null}
          </div>
        </Panel>

        <Panel title="Or try a sample">
          {samples.length === 0 ? (
            <EmptyState>No samples are available on this deployment.</EmptyState>
          ) : (
            <ul className="divide-y divide-ink-700">
              {samples.map((sample) => (
                <li key={sample.name} className="flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium text-chalk-100">
                      {sample.description}
                    </p>
                    <p className="tnum mt-0.5 text-2xs text-chalk-600">
                      {sample.filename}
                      {sample.bytes ? ` · ${Math.round(sample.bytes / 1024)} KB` : ""}
                    </p>
                  </div>
                  {sample.available ? (
                    <a className="btn-quiet shrink-0" href={api.importSampleUrl(sample.name)} download>
                      Download
                    </a>
                  ) : (
                    <StatusPill tone="caution">not built</StatusPill>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="What PRI reads">
        {spec === null ? (
          <EmptyState>Loading the sheet contract…</EmptyState>
        ) : (
          <ul className="divide-y divide-ink-700">
            {spec.sheets.map((sheet) => (
              <li key={sheet.name} className="px-4 py-3">
                <div className="flex items-baseline gap-2">
                  <h3 className="tnum text-xs font-semibold text-chalk-100">{sheet.name}</h3>
                  {sheet.required ? null : <StatusPill>optional</StatusPill>}
                  <span className="tnum ml-auto text-2xs text-chalk-600">
                    max {sheet.max_rows.toLocaleString("en-GB")} rows
                  </span>
                </div>
                <p className="mt-1 text-2xs leading-relaxed text-chalk-600">{sheet.help}</p>
                <p className="mt-1.5 text-2xs leading-relaxed text-chalk-400">
                  {sheet.columns.map((column, index) => (
                    <span key={column.name}>
                      {index > 0 ? <span className="text-chalk-600"> · </span> : null}
                      <span
                        className={column.required ? "text-chalk-200" : "text-chalk-600"}
                        title={`${column.help}${column.format ? ` (${column.format})` : ""}`}
                      >
                        {column.name}
                        {column.required ? "" : "?"}
                      </span>
                    </span>
                  ))}
                </p>
              </li>
            ))}
          </ul>
        )}
        <footer className="border-t border-ink-700 px-4 py-2.5 text-2xs text-chalk-600">
          A trailing <span className="text-chalk-400">?</span> marks an optional column.
          Hover a name for its format. Already imported a production?{" "}
          <Link href="/" className="text-chalk-200 underline underline-offset-2">
            Back to the overview
          </Link>
          .
        </footer>
      </Panel>
    </div>
  );
}

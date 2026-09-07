"use client";

/**
 * The upload control for a production workbook.
 *
 * Self-contained: it owns the drag state, the client-side rejections, the XHR
 * that reports real progress, and the cancel/retry path. The parent gets one
 * callback with a staging id.
 *
 * Two details are load-bearing and easy to get wrong:
 *
 * 1. `dragleave` fires every time the pointer crosses into a *child* element,
 *    so a zone that clears itself on `dragleave` flickers the entire time the
 *    file is over it. The fix is a depth counter — incremented on `dragenter`,
 *    decremented on `dragleave`, inactive only at zero. `relatedTarget` alone
 *    does not solve it: it is null on some cross-document transitions and
 *    Safari has historically not populated it during a drag at all.
 *
 * 2. A file dropped anywhere the page does not handle makes the *browser*
 *    navigate to it, silently destroying whatever the user had on screen. So
 *    `dragover` and `drop` are cancelled at the window level regardless of
 *    where the pointer is, and the drop target is the whole page rather than a
 *    small rectangle the user has to aim at.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ImportReport } from "@/lib/types";

/**
 * The upload lifecycle, written down.
 *
 * `dragActive` is a resting state with a file hovering over it: leaving the
 * drag returns to whichever resting state it interrupted, so hovering a file
 * over a rejection message and then thinking better of it does not erase the
 * reason the last attempt failed.
 */
export type DropZonePhase =
  | "idle"
  | "dragActive"
  | "validating"
  | "uploading"
  | "analyzing"
  | "done"
  | "rejected"
  | "failed";

const RESTING: readonly DropZonePhase[] = ["idle", "done", "rejected", "failed"];

export interface DropZoneProps {
  /** Comma-separated extension list, e.g. `".xlsx,.xlsm,.zip"`. */
  accept: string;
  maxBytes: number;
  onUploaded: (stagingId: string) => void;
  disabled?: boolean;
  /**
   * Accept a drop anywhere on the page, not just inside the zone. On by
   * default: a 1st AD dropping a board is aiming at a browser window, not at a
   * dashed rectangle.
   */
  wholePage?: boolean;
}

interface Outcome {
  tone: "alert" | "clear" | "caution";
  text: string;
  /** Set when the failure is worth another attempt with the same file. */
  retryable?: boolean;
}

export function DropZone({
  accept,
  maxBytes,
  onUploaded,
  disabled = false,
  wholePage = true,
}: DropZoneProps) {
  const [phase, setPhase] = useState<DropZonePhase>("idle");
  const [progress, setProgress] = useState(0);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [filename, setFilename] = useState<string | null>(null);

  const depth = useRef(0);
  const restingPhase = useRef<DropZonePhase>("idle");
  const inputRef = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const lastFile = useRef<File | null>(null);

  const extensions = useMemo(
    () =>
      accept
        .split(",")
        .map((part) => part.trim().toLowerCase())
        .filter((part) => part.startsWith(".")),
    [accept],
  );

  const busy = phase === "validating" || phase === "uploading" || phase === "analyzing";
  const blocked = disabled || busy;

  // -------------------------------------------------------------------------
  // Upload
  // -------------------------------------------------------------------------

  const reject = useCallback((text: string) => {
    setPhase("rejected");
    restingPhase.current = "rejected";
    setOutcome({ tone: "alert", text });
  }, []);

  const send = useCallback(
    (file: File) => {
      lastFile.current = file;
      setFilename(file.name);
      setProgress(0);
      setOutcome(null);
      setPhase("uploading");
      restingPhase.current = "uploading";

      const abort = new AbortController();
      controller.current = abort;

      const body = new FormData();
      body.append("file", file, file.name);

      const xhr = new XMLHttpRequest();
      // Same-origin, to the proxy. No credential is set here: the browser
      // does not have one, and the proxy adds it server-side.
      xhr.open("POST", api.uploadUrl());
      abort.signal.addEventListener("abort", () => xhr.abort(), { once: true });

      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) setProgress(event.loaded / event.total);
      });

      // The bytes are gone but the server has not answered: parsing a 500-row
      // board takes a moment, and a progress bar frozen at 100% looks hung.
      xhr.upload.addEventListener("load", () => {
        setProgress(1);
        setPhase("analyzing");
        restingPhase.current = "analyzing";
      });

      xhr.addEventListener("load", () => {
        controller.current = null;
        if (xhr.status === 413) {
          reject(`That file is larger than the ${formatBytes(maxBytes)} limit.`);
          return;
        }
        if (xhr.status === 401 || xhr.status === 403) {
          setPhase("failed");
          restingPhase.current = "failed";
          setOutcome({
            tone: "alert",
            text:
              "The API rejected the request. The web service's PRI_API_KEY does " +
              "not match the API's.",
          });
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          setPhase("failed");
          restingPhase.current = "failed";
          setOutcome({
            tone: "alert",
            text: `The server answered ${xhr.status}. ${detailOf(xhr.responseText)}`,
            retryable: true,
          });
          return;
        }

        let report: ImportReport;
        try {
          report = JSON.parse(xhr.responseText) as ImportReport;
        } catch {
          setPhase("failed");
          restingPhase.current = "failed";
          setOutcome({
            tone: "alert",
            text: "The server's answer was not readable. Try again.",
            retryable: true,
          });
          return;
        }

        setPhase("done");
        restingPhase.current = "done";
        setOutcome(
          report.duplicate_of
            ? {
                tone: "caution",
                text: "This exact file is already awaiting review — opening that one.",
              }
            : { tone: "clear", text: "Uploaded. Opening the review…" },
        );
        onUploaded(report.duplicate_of ?? report.staging_id);
      });

      xhr.addEventListener("error", () => {
        controller.current = null;
        setPhase("failed");
        restingPhase.current = "failed";
        setOutcome({
          tone: "alert",
          text: "The upload could not reach the API. Check that it is running.",
          retryable: true,
        });
      });

      xhr.addEventListener("abort", () => {
        controller.current = null;
        setPhase("idle");
        restingPhase.current = "idle";
        setProgress(0);
        setOutcome({ tone: "caution", text: "Upload cancelled." });
      });

      xhr.send(body);
    },
    [maxBytes, onUploaded, reject],
  );

  // -------------------------------------------------------------------------
  // Client-side rejection
  // -------------------------------------------------------------------------

  /**
   * Everything that can be known about a file without sending it.
   *
   * The server checks all of this again — a browser is not a trust boundary —
   * but catching it here saves a 10 MB round trip to be told the extension was
   * wrong, and gives the user the reason next to the control they used.
   */
  const accepting = useCallback(
    async (files: File[], items?: DataTransferItemList) => {
      setPhase("validating");
      restingPhase.current = "validating";

      if (files.length === 0) {
        reject("No file was dropped. Drop one workbook, or use the file picker.");
        return;
      }
      if (files.length > 1) {
        reject(`${files.length} files were dropped. PRI imports one workbook at a time.`);
        return;
      }

      const file = files[0];
      if (file === undefined) {
        reject("That file could not be read.");
        return;
      }

      if (await isDirectory(file, items?.[0])) {
        reject(
          "That is a folder. If the board is a set of CSVs, zip the folder and drop the .zip.",
        );
        return;
      }
      if (file.size === 0) {
        reject("That file is empty (0 bytes). It may still be syncing from OneDrive.");
        return;
      }
      if (file.size > maxBytes) {
        reject(
          `That file is ${formatBytes(file.size)}, over the ${formatBytes(maxBytes)} limit. ` +
            "A production board should be well under it — check for embedded images.",
        );
        return;
      }

      const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!extensions.includes(extension)) {
        reject(
          `PRI reads ${extensions.join(", ")}. ` +
            (extension === ".xls"
              ? "This is the old binary Excel format — open it and Save As .xlsx."
              : `That file is ${extension || "extensionless"}.`),
        );
        return;
      }

      send(file);
    },
    [extensions, maxBytes, reject, send],
  );

  // -------------------------------------------------------------------------
  // Drag, drop, paste
  // -------------------------------------------------------------------------

  const enterDrag = useCallback(() => {
    depth.current += 1;
    if (depth.current === 1) {
      setPhase((current) => {
        if (!RESTING.includes(current)) return current;
        restingPhase.current = current;
        return "dragActive";
      });
    }
  }, []);

  const leaveDrag = useCallback(() => {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) {
      setPhase((current) => (current === "dragActive" ? restingPhase.current : current));
    }
  }, []);

  const handleDrop = useCallback(
    (event: DragEvent | React.DragEvent) => {
      event.preventDefault();
      depth.current = 0;
      setPhase((current) => (current === "dragActive" ? restingPhase.current : current));
      if (blocked) return;

      const transfer = event.dataTransfer;
      if (!transfer) return;
      void accepting(Array.from(transfer.files), transfer.items);
    },
    [accepting, blocked],
  );

  // A file dropped on any part of the page the app does not handle is a
  // navigation away from the app. Cancel it everywhere, always — including
  // while the zone is disabled, where doing nothing is the correct behaviour
  // and losing the page is not.
  useEffect(() => {
    const swallow = (event: DragEvent) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = blocked ? "none" : "copy";
    };
    const onDrop = (event: DragEvent) => {
      if (wholePage) handleDrop(event);
      else event.preventDefault();
    };

    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", onDrop);
    if (wholePage) {
      window.addEventListener("dragenter", enterDrag);
      window.addEventListener("dragleave", leaveDrag);
    }
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragenter", enterDrag);
      window.removeEventListener("dragleave", leaveDrag);
    };
  }, [blocked, enterDrag, handleDrop, leaveDrag, wholePage]);

  // Copying a file in Explorer or Finder and pressing Ctrl+V on the page is a
  // real habit, and it costs six lines to honour it.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0 || blocked) return;
      event.preventDefault();
      void accepting(files, event.clipboardData?.items);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [accepting, blocked]);

  // A component that unmounts mid-upload should not leave the request running.
  useEffect(() => () => controller.current?.abort(), []);

  const openPicker = useCallback(() => {
    if (!blocked) inputRef.current?.click();
  }, [blocked]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const dragging = phase === "dragActive";
  const border = dragging
    ? "border-seal bg-seal-wash"
    : phase === "rejected" || phase === "failed"
      ? "border-stamp-dim bg-stamp-wash"
      : "border-board-500 bg-board-800 hover:border-board-500";

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={blocked ? -1 : 0}
        aria-disabled={blocked}
        aria-label={`Upload a production workbook. Accepts ${extensions.join(", ")} up to ${formatBytes(maxBytes)}.`}
        onClick={openPicker}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openPicker();
          }
        }}
        onDragEnter={wholePage ? undefined : enterDrag}
        onDragLeave={wholePage ? undefined : leaveDrag}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = blocked ? "none" : "copy";
        }}
        onDrop={wholePage ? undefined : handleDrop}
        className={`flex min-h-[190px] cursor-pointer flex-col items-center justify-center gap-3
                    rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors
                    focus:outline-none focus-visible:ring-2 focus-visible:ring-seal
                    ${blocked ? "cursor-not-allowed opacity-60" : ""} ${border}`}
      >
        {busy ? (
          <Progress phase={phase} progress={progress} filename={filename} />
        ) : (
          <>
            <p className="text-sm font-medium text-chalk-100">
              {dragging ? "Drop it anywhere on this page" : "Drop your production board here"}
            </p>
            <p className="max-w-sm text-2xs leading-relaxed text-chalk-600">
              {extensions.join(" · ")} — up to {formatBytes(maxBytes)}. Nothing is written
              to the production until you confirm the review on the next screen.
            </p>
            <span className="btn-quiet mt-1" aria-hidden="true">
              Choose a file
            </span>
            <p className="text-2xs text-chalk-600">or press Ctrl+V to paste a copied file</p>
          </>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        tabIndex={-1}
        disabled={blocked}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          // Reset first: picking the same file twice in a row fires no change
          // event otherwise, which reads as a dead button after a rejection.
          event.target.value = "";
          if (files.length > 0) void accepting(files);
        }}
      />

      {/* One region for every state change, so a screen reader hears the
          rejection reason rather than watching a colour change. */}
      <div role="status" aria-live="polite" className="min-h-[20px]">
        {outcome ? (
          <p
            className={`text-xs leading-relaxed ${
              outcome.tone === "alert"
                ? "text-stamp"
                : outcome.tone === "clear"
                  ? "text-seal"
                  : "text-caution"
            }`}
          >
            {outcome.text}
          </p>
        ) : busy ? (
          <p className="text-xs text-chalk-400">
            {phase === "validating"
              ? "Checking the file…"
              : phase === "uploading"
                ? `Uploading ${filename} — ${Math.round(progress * 100)}%`
                : "Reading the workbook…"}
          </p>
        ) : null}
      </div>

      <div className="flex gap-2">
        {phase === "uploading" ? (
          <button
            type="button"
            className="btn-stop"
            onClick={() => controller.current?.abort()}
          >
            Cancel upload
          </button>
        ) : null}
        {outcome?.retryable && lastFile.current ? (
          <button
            type="button"
            className="btn-quiet"
            onClick={() => {
              const file = lastFile.current;
              if (file) send(file);
            }}
          >
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

function Progress({
  phase,
  progress,
  filename,
}: {
  phase: DropZonePhase;
  progress: number;
  filename: string | null;
}) {
  const indeterminate = phase === "analyzing" || phase === "validating";
  return (
    <div className="w-full max-w-sm space-y-2">
      <p className="truncate text-sm font-medium text-chalk-100">{filename ?? "Working…"}</p>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-board-600"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : Math.round(progress * 100)}
      >
        <div
          className={`h-full rounded-full bg-seal transition-[width] duration-150 ${
            indeterminate ? "animate-pulse" : ""
          }`}
          style={{ width: indeterminate ? "100%" : `${Math.round(progress * 100)}%` }}
        />
      </div>
      <p className="text-2xs text-chalk-600">
        {phase === "validating"
          ? "checking"
          : phase === "uploading"
            ? `uploading ${Math.round(progress * 100)}%`
            : "reading the workbook"}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Tell a dropped folder from a dropped file.
 *
 * A folder arrives in `dataTransfer.files` looking like a zero-type file, so
 * the extension check would reject it with a misleading reason. The drag API's
 * `webkitGetAsEntry` answers directly and is supported everywhere that matters;
 * when it is unavailable — a paste, or the file picker — reading one byte is
 * the reliable fallback, because a directory handle throws instead.
 */
async function isDirectory(file: File, item?: DataTransferItem): Promise<boolean> {
  const entry = item?.webkitGetAsEntry?.();
  if (entry) return entry.isDirectory;
  if (file.type !== "" || file.name.includes(".")) return false;
  try {
    await file.slice(0, 1).arrayBuffer();
    return false;
  } catch {
    return true;
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  const megabytes = bytes / (1024 * 1024);
  return `${megabytes >= 10 ? Math.round(megabytes) : megabytes.toFixed(1)} MB`;
}

/** Pull the API's `detail` out of an error body without assuming it is JSON. */
function detailOf(body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : "";
  } catch {
    return "";
  }
}

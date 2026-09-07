/**
 * Server-side proxy to the FastAPI service.
 *
 * The browser never talks to the backend directly. Every call goes here first,
 * and this handler adds the API key on the server. That solves three problems
 * with one piece of code:
 *
 *   Auth. Mutating routes require `X-API-Key`. A judge opening the hosted demo
 *   has no key, and shipping one to the browser is the same as having none.
 *   The key lives in the web service's environment and never leaves it.
 *
 *   Build-time config. `NEXT_PUBLIC_API_URL` was baked into the bundle at
 *   `docker build`, so the image only worked against the backend URL it was
 *   built for — and the backend URL is not known until Cloud Run creates the
 *   service. `process.env` is read here per request, at runtime.
 *
 *   CORS. Same origin, so there is nothing to negotiate.
 *
 * Bodies are streamed rather than buffered. That matters most for SSE, where
 * buffering would collect the entire recovery timeline and deliver it in one
 * burst at the end — destroying the twenty seconds of the demo that show the
 * pipeline thinking — but it also keeps PDF and .xlsx downloads intact and
 * lets a 10 MB workbook upload through without being re-encoded.
 */

import { NextRequest } from "next/server";

/** Node, not Edge: the Edge runtime has no undici duplex streaming for uploads. */
export const runtime = "nodejs";

/** Never cache a proxied response — every one is live state. */
export const dynamic = "force-dynamic";

/**
 * Headers that describe *this* hop and must not be replayed upstream.
 * `host` in particular would make FastAPI generate wrong absolute URLs, and a
 * stale `content-length` breaks a streamed body.
 */
const HOP_BY_HOP = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-authorization",
  "proxy-authenticate",
  "te",
  "trailer",
  "content-length",
  "accept-encoding",
]);

/** Response headers we must not copy back — they describe the upstream hop. */
const STRIP_FROM_RESPONSE = new Set([
  "content-encoding",
  "content-length",
  "transfer-encoding",
  "connection",
]);

function upstreamBase(): string {
  // Read at request time, never at build time. That is the whole point.
  return (
    process.env.PRI_API_BASE_URL ??
    process.env.API_BASE_URL ??
    "http://localhost:8000"
  ).replace(/\/+$/, "");
}

function forwardedHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });

  // The credential the browser does not have and must never be given.
  const apiKey = process.env.PRI_API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  return headers;
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const search = request.nextUrl.search;
  const target = `${upstreamBase()}/${path.join("/")}${search}`;

  const method = request.method;
  const hasBody = method !== "GET" && method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers: forwardedHeaders(request),
      // Stream the request body straight through. `duplex: "half"` is required
      // by undici whenever body is a stream; without it Node throws. This is
      // what lets a 10 MB multipart upload pass without being read into memory
      // and re-encoded.
      body: hasBody ? request.body : undefined,
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
  } catch (cause) {
    // The backend is unreachable. Answer in the same envelope the API uses for
    // its own errors, so the UI's error handling does not need a special case.
    return Response.json(
      {
        error: "upstream_unreachable",
        detail:
          cause instanceof Error
            ? `Could not reach the PRI API: ${cause.message}`
            : "Could not reach the PRI API.",
        step: null,
        rule_code: null,
        rule_codes: [],
      },
      { status: 502 },
    );
  }

  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIP_FROM_RESPONSE.has(key.toLowerCase())) headers.set(key, value);
  });

  // Server-sent events must arrive incrementally. Any buffering layer between
  // here and the browser turns the live pipeline into one burst at the end, so
  // say so explicitly — X-Accel-Buffering is honoured by nginx-family proxies.
  if ((upstream.headers.get("content-type") ?? "").includes("text/event-stream")) {
    headers.set("Content-Type", "text/event-stream");
    headers.set("Cache-Control", "no-cache, no-transform");
    headers.set("Connection", "keep-alive");
    headers.set("X-Accel-Buffering", "no");
  }

  // The body is passed through as a stream and never decoded to text, which is
  // what keeps PDFs and .xlsx files byte-identical. Status and body are
  // forwarded verbatim including errors — the review screen renders the typed
  // issue list straight out of a 4xx body.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

export async function PUT(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

export async function PATCH(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

export async function HEAD(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

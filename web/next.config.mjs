import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

// A stray package-lock.json in the user's home directory makes Next guess the
// wrong workspace root and trace files from outside the project into the
// standalone output. Pin it to this directory. `fileURLToPath` rather than
// `URL.pathname`, which yields a leading-slash path Windows cannot resolve.
const projectRoot = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: projectRoot,
  // Cloud Run runs the built server directly; standalone keeps the image small
  // by tracing only the files the server actually needs.
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
    NEXT_PUBLIC_PRODUCTION_ID: process.env.NEXT_PUBLIC_PRODUCTION_ID ?? "film-001",
  },
};

export default nextConfig;

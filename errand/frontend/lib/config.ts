// Backend base URL. Configurable via NEXT_PUBLIC_BACKEND_URL; defaults to the
// local FastAPI server. The backend owns every secret — the browser only ever
// talks to these public endpoints.
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL?.replace(/\/$/, "") ||
  "http://localhost:8787";

export const api = (path: string) => `${BACKEND_URL}${path}`;

// WebSocket URL for the same backend. The voice relay lives at
// /api/voice/ws; we derive the ws(s):// origin from the http(s):// base so a
// single NEXT_PUBLIC_BACKEND_URL configures both transports.
export const wsApi = (path: string) => {
  const wsBase = BACKEND_URL.replace(/^http/, "ws");
  return `${wsBase}${path}`;
};

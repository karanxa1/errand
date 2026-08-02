// Backend base URL. Configurable via NEXT_PUBLIC_BACKEND_URL; defaults to the
// local FastAPI server. The backend owns every secret — the browser only ever
// talks to these public endpoints.
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL?.replace(/\/$/, "") ||
  "http://localhost:8787";

export const api = (path: string) => `${BACKEND_URL}${path}`;

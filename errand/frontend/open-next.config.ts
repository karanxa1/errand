import { defineCloudflareConfig } from "@opennextjs/cloudflare";

// Minimal OpenNext config for Cloudflare Workers. The Errand frontend is a
// streaming chat client that holds all state in the browser and talks to the
// FastAPI backend over HTTPS/WSS — it needs no Next.js ISR cache, so we omit the
// R2 incremental cache override to keep the deploy dependency-free.
export default defineCloudflareConfig();

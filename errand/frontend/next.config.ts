import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;

// Lets `next dev` integrate with the OpenNext Cloudflare adapter (and access
// Cloudflare bindings locally). Guarded to dev only: during `next build` (incl.
// the build OpenNext runs on CI) it must NOT boot the Workers runtime, which
// otherwise fails with SQLITE_BUSY on the CI runner.
if (process.env.NODE_ENV === "development") {
  import("@opennextjs/cloudflare").then(({ initOpenNextCloudflareForDev }) => {
    initOpenNextCloudflareForDev();
  });
}

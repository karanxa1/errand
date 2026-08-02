// Tailwind v4's PostCSS plugin, and nothing else.
//
// Two things about this file are load-bearing:
//
// 1. It must exist HERE, in the app directory. Without a local config Next walks
//    UP the directory tree and picks up the stale root scaffold's config, which
//    is not a dependency of this app and broke the CI build once already
//    (see 4a17db7).
// 2. Do NOT pass a `base` option to @tailwindcss/postcss. It resolves fine in
//    dev and then breaks the Cloudflare Workers production build, which is where
//    this app actually ships.
export default { plugins: { "@tailwindcss/postcss": {} } };

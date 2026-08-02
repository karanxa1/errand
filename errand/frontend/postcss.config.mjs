// The Errand frontend uses plain CSS Modules — no PostCSS plugins needed.
// This empty config exists so Next.js stops here instead of walking UP the
// directory tree and picking up the stale root scaffold's Tailwind PostCSS
// config (which isn't a dependency of this app and breaks the CI build).
export default { plugins: {} };

import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

// Test-only configuration. Next.js' tsconfig sets `jsx: "preserve"`, which under
// Vitest 4 (rolldown-vite / oxc) leaves literal JSX untransformed and breaks any
// test that renders a component with `render(<Component />)`. Overriding the oxc
// JSX transform here fixes that at the root without touching the app's tsconfig.
// The `@/` alias mirrors tsconfig `paths` so tests and components share imports.
// `next build` never reads this file, so it cannot collide with a concurrent build.
export default defineConfig({
  // `globals: true` makes afterEach available as a global, which is what lets
  // @testing-library/react register its automatic `cleanup` between tests. Without
  // it, successive render() calls in one file accumulate in the DOM and queries
  // find duplicate elements. Existing tests import their helpers explicitly, so
  // this is purely additive.
  test: {
    globals: true,
  },
  resolve: {
    alias: { "@": resolve(import.meta.dirname, ".") },
  },
  oxc: {
    jsx: "react-jsx",
    jsxImportSource: "react",
  },
});

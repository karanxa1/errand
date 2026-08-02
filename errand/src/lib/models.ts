/**
 * Model selector config — single source of truth for the UI selector AND the
 * Deepgram `think` provider / chat route.
 *
 * Sol / Terra / Luna are REAL gpt-5.6 model IDs (verified on the account):
 * gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna. The UI renders `label` + `logo`;
 * the server sends `id` straight to OpenAI.
 */

export type ModelId = "gpt-5.6-sol" | "gpt-5.6-terra" | "gpt-5.6-luna";

export interface ModelOption {
  /** Stable key used in requests + persisted selection. */
  key: "sol" | "terra" | "luna";
  /** Brand display name shown in the selector. */
  label: string;
  /** Short tagline under the name. */
  tagline: string;
  /** Emoji glyph fallback; swap for an SVG path in /public if desired. */
  logo: string;
  /** Real OpenAI model id used server-side. */
  id: ModelId;
}

export const MODELS: ModelOption[] = [
  {
    key: "sol",
    label: "Sol",
    tagline: "Flagship — most capable",
    logo: "☀️",
    id: "gpt-5.6-sol",
  },
  {
    key: "terra",
    label: "Terra",
    tagline: "Balanced — everyday",
    logo: "🌍",
    id: "gpt-5.6-terra",
  },
  {
    key: "luna",
    label: "Luna",
    tagline: "Fastest — lightweight",
    logo: "🌙",
    id: "gpt-5.6-luna",
  },
];

export const DEFAULT_MODEL_KEY: ModelOption["key"] = "sol";

export function resolveModel(key: string | undefined): ModelOption {
  return (
    MODELS.find((m) => m.key === key) ??
    MODELS.find((m) => m.key === DEFAULT_MODEL_KEY)!
  );
}

/* ToolIcons — bespoke tool-call iconography, drawn for this brand in one shared
   stroke language: 1.7px strokes, round caps/joins, a single filled focal node,
   and one small "signal" detail. Not an icon pack, no emoji. `currentColor`
   lets each mark take the surrounding tone (the card sets the colour). */

interface IconProps {
  size?: number;
}

const base = {
  fill: "none" as const,
  "aria-hidden": true as const,
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

// Policy / grounding — a shield with a tick, the "checked against policy" mark.
export function PolicyIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="M10 2.5l6 2v5c0 3.7-2.5 6.6-6 7.5C6.5 16.1 4 13.2 4 9.5v-5l6-2Z" stroke="currentColor" />
      <path d="M7.3 10l1.9 1.9L13 8" stroke="currentColor" />
    </svg>
  );
}

// Cart — a basket with a load handle and one filled wheel (motion with intent).
export function CartIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="M3 4h1.7l1.4 8.2h8.2l1.4-6H6" stroke="currentColor" />
      <circle cx="8" cy="16" r="1.25" fill="currentColor" stroke="none" />
      <circle cx="14.4" cy="16" r="1.25" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Secure payment session — a padlock, its shackle open toward the light source.
export function LockIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <rect x="4.5" y="8.5" width="11" height="8" rx="2" stroke="currentColor" />
      <path d="M7 8.5V6.4a3 3 0 0 1 6 0V8.5" stroke="currentColor" />
      <circle cx="10" cy="12.2" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}

// One-time card credential — a card with a dispatch spark (issued, single-use).
export function CardIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <rect x="2.5" y="5" width="12" height="9" rx="2" stroke="currentColor" />
      <path d="M2.5 8.2h12" stroke="currentColor" />
      <path d="M5 11.4h3" stroke="currentColor" />
      <path d="M16.4 4.2l1 .6M17.6 6.6l-1 .6M18.8 3.2l-.4 1.1" stroke="currentColor" opacity="0.9" />
    </svg>
  );
}

// Checkout / order placed — a shopping bag with a tick.
export function BagIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="M5 6.5h10l-.8 9.2a1 1 0 0 1-1 .9H6.8a1 1 0 0 1-1-.9L5 6.5Z" stroke="currentColor" />
      <path d="M7.4 6.5V5.6a2.6 2.6 0 0 1 5.2 0v.9" stroke="currentColor" />
      <path d="M7.7 11l1.6 1.6L12.6 9" stroke="currentColor" />
    </svg>
  );
}

// Mail / confirmation — an envelope with the flap open and a small seal node.
export function MailIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <rect x="3" y="5" width="14" height="10" rx="2" stroke="currentColor" />
      <path d="M3.6 6l6.4 4.6L16.4 6" stroke="currentColor" />
      <circle cx="15.3" cy="12.6" r="1.05" fill="currentColor" stroke="none" opacity="0.9" />
    </svg>
  );
}

// Inbox ready — an at-sign, drawn open (the agent's own address is live).
export function InboxIcon({ size = 16 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <circle cx="10" cy="10" r="3" stroke="currentColor" />
      <path d="M13 7.6v3.3a2 2 0 0 0 3.4 1.3A7 7 0 1 0 13.4 15" stroke="currentColor" />
    </svg>
  );
}

// A generic node — for unknown/unmapped events, the brand's filled focal dot.
export function NodeIcon({ size = 16 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <circle cx="10" cy="10" r="5.5" stroke="currentColor" />
      <circle cx="10" cy="10" r="1.7" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Warning / signal — a triangle with a bang, for over-budget / declined / abort.
export function AlertIcon({ size = 16 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="M10 3.2l7 12H3l7-12Z" stroke="currentColor" />
      <path d="M10 8v3.4" stroke="currentColor" />
      <circle cx="10" cy="13.6" r="0.95" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Web search — a lens over a horizon line, with a small "found" signal node in
// the glass. Drawn in the same stroke language (not a stock magnifier).
export function SearchIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <circle cx="8.6" cy="8.6" r="5.1" stroke="currentColor" />
      <path d="M6 8.6h5.2" stroke="currentColor" opacity="0.55" />
      <path d="M12.4 12.4l3.4 3.4" stroke="currentColor" />
      <circle cx="8.6" cy="8.6" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Voice — a spoken-waveform mark: uneven bars (longest at the light-source side)
// rising from a baseline, with a single filled focal node. The agent's voice.
export function VoiceIcon({ size = 18 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" {...base}>
      <path d="M5 8.4v3.2M8 5.6v8.8M11 7.2v5.6M14 4.4v11.2" stroke="currentColor" />
      <circle cx="8" cy="4.6" r="1.05" fill="currentColor" stroke="none" opacity="0.85" />
    </svg>
  );
}

// A small check — status "done".
export function CheckIcon({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" {...base}>
      <path d="M3 8.4l3 3 7-7" stroke="currentColor" strokeWidth={1.8} />
    </svg>
  );
}

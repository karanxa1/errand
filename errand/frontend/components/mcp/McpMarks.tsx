/* Marks for the MCP panel, drawn in the language of components/Marks.tsx:
   1.4-1.7px strokes, round caps, one filled focal node, currentColor throughout.
   No icon pack, and no tile behind any of them. */

/** A server: a socket a tool provider plugs into. Two leads into a filled body. */
export function ServerMark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M5 9.5h14M5 9.5v6a1.5 1.5 0 0 0 1.5 1.5h11a1.5 1.5 0 0 0 1.5-1.5v-6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path d="M9 9.5V6M15 9.5V6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="13.5" r="1.9" fill="currentColor" />
    </svg>
  );
}

/** Connected: the route completes and lands. A tick drawn as a path, not a glyph. */
export function ConnectedMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M2.5 8.6l3.1 3.1 7-7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Needs authorizing: a key, turned. The bit is the signal detail. */
export function KeyMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="5.6" cy="5.6" r="3" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M7.9 7.9l5 5M11 11.2l1.4-1.4M12.4 12.6l1.2-1.2"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** An error: a stopped run. A stem with a filled terminal node. */
export function AlertMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 3.4v5.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="8" cy="11.7" r="1.15" fill="currentColor" />
    </svg>
  );
}

/** Untested: a waypoint not yet reached. Deliberately hollow. */
export function IdleMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="4.4" stroke="currentColor" strokeWidth="1.5" opacity="0.8" />
    </svg>
  );
}

/** A tool: a wrench head, cut open. Reads at 14px, which a realistic one does not. */
export function ToolMark({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M9.9 3.3a2.9 2.9 0 0 0 3.6 3.9l-6 6a1.7 1.7 0 0 1-2.4-2.4l6-6a2.9 2.9 0 0 0-1.2-1.5"
        stroke="currentColor"
        strokeWidth="1.45"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="5.6" cy="11.9" r="0.95" fill="currentColor" />
    </svg>
  );
}

/** Close: two strokes meeting, round-capped. */
export function CloseMark({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M4.2 4.2l7.6 7.6M11.8 4.2l-7.6 7.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Retest: an arc returning on itself, with the arrowhead as two short strokes. */
export function RetryMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M13 8a5 5 0 1 1-1.7-3.75"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M13.2 1.9v2.7h-2.7"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Remove: the trash mark from Sidebar.tsx, kept identical so the language holds. */
export function TrashMark({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3.5 4.5h9M6.5 4.5V3.4a.9.9 0 0 1 .9-.9h1.2a.9.9 0 0 1 .9.9V4.5M5 4.5l.5 8a1 1 0 0 0 1 .95h3a1 1 0 0 0 1-.95l.5-8"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Disclosure chevron. Rotated by the caller. */
export function ChevronMark({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M4 6l4 4 4-4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

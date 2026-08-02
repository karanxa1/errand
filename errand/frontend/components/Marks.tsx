/* Marks — bespoke SVG iconography drawn for this brand. No icon pack, no emoji.
   Shared construction language: 1.6px strokes, round caps, a single filled
   focal node, and one small "signal" detail. currentColor lets each mark take
   the surrounding tone. */

export function ErrandMark({ size = 26 }: { size?: number }) {
  // An errand = a route to a waypoint. A path that turns and lands on a filled
  // node, with a small dispatch spark leaving it — motion with intent.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 23 C 6 15, 12 15, 16 15 C 21 15, 24 12, 24 8"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle cx="6" cy="23" r="2.4" fill="currentColor" />
      <path
        d="M24 8 l 2.6 1.1 M24 8 l -1 2.6"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle cx="24" cy="8" r="1.3" fill="currentColor" opacity="0.85" />
    </svg>
  );
}

export function SolMark({ size = 22 }: { size?: number }) {
  // Sun — a solid core with deliberately UNEVEN rays (not the symmetric emoji
  // radial), longest toward upper-left to match the app's light source.
  const rays = [
    { a: -125, len: 6.5 },
    { a: -70, len: 4.5 },
    { a: -20, len: 5.2 },
    { a: 30, len: 4 },
    { a: 80, len: 5 },
    { a: 135, len: 4.4 },
    { a: 180, len: 5.6 },
  ];
  const cx = 16;
  const cy = 16;
  const r0 = 6.2;
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx={cx} cy={cy} r={r0} fill="currentColor" opacity="0.16" />
      <circle cx={cx} cy={cy} r={r0 - 2.4} fill="currentColor" />
      {rays.map((ray, i) => {
        const rad = (ray.a * Math.PI) / 180;
        const x1 = cx + Math.cos(rad) * (r0 + 1.2);
        const y1 = cy + Math.sin(rad) * (r0 + 1.2);
        const x2 = cx + Math.cos(rad) * (r0 + 1.2 + ray.len);
        const y2 = cy + Math.sin(rad) * (r0 + 1.2 + ray.len);
        return (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        );
      })}
    </svg>
  );
}

export function TerraMark({ size = 22 }: { size?: number }) {
  // Earth — a globe with a tilted meridian and one landmass arc; a small node
  // marks a location. Grounded, "balanced".
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <circle cx="16" cy="16" r="9" fill="currentColor" opacity="0.14" />
      <circle
        cx="16"
        cy="16"
        r="9"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      {/* tilted meridian */}
      <ellipse
        cx="16"
        cy="16"
        rx="3.6"
        ry="9"
        stroke="currentColor"
        strokeWidth="1.4"
        transform="rotate(20 16 16)"
        opacity="0.75"
      />
      {/* landmass arc */}
      <path
        d="M9 13 c 3 -1.4, 5 1, 8 0.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="19.5" cy="19.5" r="1.6" fill="currentColor" />
    </svg>
  );
}

export function LunaMark({ size = 22 }: { size?: number }) {
  // Moon — a crescent carved by an offset mask, with two small craters. Quiet,
  // "fast/light". The crescent opens toward the app's light source.
  const id = "luna-cut";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <defs>
        <mask id={id}>
          <rect width="32" height="32" fill="white" />
          <circle cx="20.5" cy="13" r="8.4" fill="black" />
        </mask>
      </defs>
      <circle
        cx="15"
        cy="16"
        r="8.6"
        fill="currentColor"
        opacity="0.18"
        mask={`url(#${id})`}
      />
      <circle
        cx="15"
        cy="16"
        r="8.6"
        fill="currentColor"
        mask={`url(#${id})`}
      />
      <circle cx="12.2" cy="18.6" r="1.15" fill="var(--ink-100, #0f1713)" opacity="0.55" />
      <circle cx="11" cy="14.4" r="0.8" fill="var(--ink-100, #0f1713)" opacity="0.45" />
    </svg>
  );
}

export function markFor(key: string, size = 22) {
  switch (key) {
    case "sol":
      return <SolMark size={size} />;
    case "terra":
      return <TerraMark size={size} />;
    case "luna":
      return <LunaMark size={size} />;
    default:
      return <SolMark size={size} />;
  }
}

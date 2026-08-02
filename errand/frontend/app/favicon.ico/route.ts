const ICON = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#07100c"/>
  <path d="M15 39c7 0 7-14 14-14h7c7 0 7-9 13-9" fill="none" stroke="#13ef93" stroke-width="5" stroke-linecap="round"/>
  <circle cx="15" cy="39" r="3.5" fill="#13ef93"/>
  <circle cx="49" cy="16" r="3.5" fill="#13ef93"/>
</svg>`;

export function GET() {
  return new Response(ICON, {
    headers: {
      "Content-Type": "image/svg+xml",
      "Cache-Control": "public, max-age=86400",
    },
  });
}

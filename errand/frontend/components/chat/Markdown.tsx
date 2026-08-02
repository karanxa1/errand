"use client";

/* Markdown for assistant text — safe to render MID-STREAM.
 *
 * Two things make this non-trivial and both are load-bearing:
 *
 * 1. STREAMING. Tokens arrive a few characters at a time, so every render sees
 *    half-finished markdown. Most of it degrades gracefully (CommonMark renders a
 *    dangling `**` or a half-typed `[label](` as literal text), but an UNCLOSED
 *    ``` fence does not: it swallows the entire rest of the message into a code
 *    block, and the bubble visibly collapses on every token until the model
 *    finally closes it. `closeOpenMarkdown` repairs that for DISPLAY ONLY — the
 *    stored message is never mutated. Do not "simplify" it away.
 *
 * 2. SECURITY. Assistant text is model-generated, so raw HTML in it is an
 *    injection vector. react-markdown ignores embedded HTML unless `rehype-raw`
 *    is added — it must never be added here. Links are forced through
 *    target="_blank" + rel="noopener noreferrer nofollow".
 */

import { createContext, memo, useContext, useMemo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/* Module-level so the references stay stable across the live bubble's
   per-token re-renders and react-markdown never rebuilds its pipeline. */
const REMARK_PLUGINS = [remarkGfm];

/* A fence opener/closer: up to three spaces of indent, then 3+ backticks or
   tildes. Group 2 is the info string ("ts", "json"); a CLOSING fence carries
   none, which is how an opener is told from a closer. */
const FENCE_LINE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

/**
 * Balance the markdown a streaming message has not finished writing yet.
 * Returns a DISPLAY copy — callers must keep storing the original text.
 *
 * Only two repairs, both for constructs that swallow following content rather
 * than degrading to literal text:
 *   - an open ``` / ~~~ fence gets a matching closer appended;
 *   - an odd backtick count on the line currently being typed gets one backtick.
 *
 * The backtick repair is deliberately scoped to the LAST line. A global odd/even
 * count would "close" a stray backtick from three paragraphs back and turn
 * everything after it into one long inline-code span — worse than the flicker it
 * was meant to fix. The half-written span always sits on the line being streamed.
 */
export function closeOpenMarkdown(text: string): string {
  const lines = text.split("\n");
  let open: string | null = null;
  for (const line of lines) {
    const m = FENCE_LINE.exec(line);
    if (!m) continue;
    const marker = m[1];
    if (open === null) {
      open = marker;
    } else if (marker[0] === open[0] && marker.length >= open.length && m[2].trim() === "") {
      open = null;
    }
  }

  if (open !== null) {
    // Inside a fence every backtick is literal, so closing the fence is the
    // whole job.
    return text + (text.endsWith("\n") ? "" : "\n") + open;
  }

  // A fence line's own backticks are not an inline span — counting them would
  // "balance" a just-closed ``` by appending a fourth backtick.
  const lastLine = lines[lines.length - 1];
  if (FENCE_LINE.test(lastLine)) return text;
  const ticks = lastLine.match(/`/g)?.length ?? 0;
  return ticks % 2 === 1 ? text + "`" : text;
}

/* react-markdown gives fenced code as <pre><code>. Only the <pre> branch knows
   it is a block, so it tells the <code> below it through context — that is how
   inline code and block code get different treatments without guessing from a
   `language-*` class (fences without an info string carry none). */
const InPre = createContext(false);

const BLOCK = "first:mt-0 last:mb-0";

const HEADING_BASE = `mt-[16px] mb-[6px] ${BLOCK} font-semibold tracking-[-0.005em]`;

function Heading({ level, children }: { level: 1 | 2 | 3 | 4; children: ReactNode }) {
  // Sizes step down inside the bubble's own 14.5px scale — this is a chat reply,
  // not an article, so an h1 is a firm line rather than a display headline. Tone
  // is set per level (never in the base: two `text-*` colour utilities on one
  // element resolve by stylesheet order, not by class order, so the loser is
  // whichever Tailwind happened to emit first).
  const tone =
    level === 1
      ? "text-[17px] text-hi"
      : level === 2
        ? "text-[15.5px] text-hi"
        : level === 3
          ? "text-[14.5px] text-hi"
          : "text-[13px] text-mid";
  const Tag = `h${level}` as "h1";
  return <Tag className={`${HEADING_BASE} ${tone}`}>{children}</Tag>;
}

const COMPONENTS: Components = {
  p: ({ children }) => (
    <p className={`my-[10px] ${BLOCK} text-[14.5px] leading-[1.55] text-body`}>{children}</p>
  ),

  h1: ({ children }) => <Heading level={1}>{children}</Heading>,
  h2: ({ children }) => <Heading level={2}>{children}</Heading>,
  h3: ({ children }) => <Heading level={3}>{children}</Heading>,
  h4: ({ children }) => <Heading level={4}>{children}</Heading>,
  h5: ({ children }) => <Heading level={4}>{children}</Heading>,
  h6: ({ children }) => <Heading level={4}>{children}</Heading>,

  strong: ({ children }) => (
    <strong className="text-hi [font-weight:650]">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-hi">{children}</em>,
  del: ({ children }) => (
    <del className="text-low decoration-[1.5px] line-through">{children}</del>
  ),

  a: ({ href, children }) => (
    <a
      href={href}
      // Model-authored destination: never same-tab, never carrying this window,
      // never lending ranking.
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="text-green-soft underline decoration-green-dim underline-offset-[3px] transition-[color,text-decoration-color] duration-[180ms] ease-[ease] hover:text-green hover:decoration-green-soft"
    >
      {children}
    </a>
  ),

  // Tailwind preflight strips list markers and padding, so both are re-stated.
  ul: ({ children }) => (
    <ul className={`my-[10px] ${BLOCK} list-disc pl-[19px] marker:text-green-dim`}>
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className={`my-[10px] ${BLOCK} list-decimal pl-[21px] marker:text-low`}>
      {children}
    </ol>
  ),
  li: ({ children, className }) => {
    // remark-gfm marks task-list items; those carry their own checkbox, so the
    // bullet is dropped and the indent pulled back under it.
    const task = (className ?? "").includes("task-list-item");
    return (
      <li
        className={
          task
            ? "my-[3px] -ml-[19px] flex list-none items-baseline gap-2 text-[14.5px] leading-[1.55] text-body"
            : // A nested list is part of its parent item, so it tucks in tight
              // rather than taking a full block gap.
              "my-[3px] pl-[2px] text-[14.5px] leading-[1.55] text-body [&>ol]:mt-[4px] [&>ol]:mb-0 [&>ul]:mt-[4px] [&>ul]:mb-0"
        }
      >
        {children}
      </li>
    );
  },
  input: ({ checked, type }) =>
    type === "checkbox" ? (
      <input
        type="checkbox"
        checked={Boolean(checked)}
        readOnly
        disabled
        className="mt-[1px] h-[13px] w-[13px] flex-none translate-y-[2px] accent-green"
      />
    ) : null,

  // A tonal recess rather than the reflexive hairline bar — same
  // surface-plus-self-coloured-lip language the tool cards use.
  blockquote: ({ children }) => (
    <blockquote
      className={`my-[12px] ${BLOCK} rounded-[10px] bg-ink-100 px-[13px] py-[9px] text-mid shadow-[inset_0_0_0_1px_var(--color-edge)] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_p]:text-mid`}
    >
      {children}
    </blockquote>
  ),

  // Rounded caps, so a section break reads as a considered mark rather than a
  // square-ended default rule.
  hr: () => <hr className={`my-[15px] ${BLOCK} h-[2px] rounded-full border-0 bg-edge-strong`} />,

  pre: ({ children }) => (
    // The scroll lives on this container, so a long line never pushes the page
    // body sideways.
    <div
      className={`my-[12px] ${BLOCK} overflow-x-auto rounded-card bg-ink-050 px-[13px] py-[11px] shadow-[inset_0_0_0_1px_var(--color-edge)]`}
    >
      <InPre.Provider value={true}>
        <pre className="m-0 w-max min-w-full font-mono text-[12.5px] leading-[1.6] text-body">
          {children}
        </pre>
      </InPre.Provider>
    </div>
  ),
  code: ({ children }) => {
    const block = useContext(InPre);
    if (block) return <code className="bg-transparent p-0 font-mono">{children}</code>;
    return (
      <code className="rounded-[6px] bg-ink-200 px-[5px] py-[1.5px] font-mono text-[12.5px] text-hi shadow-[inset_0_0_0_1px_var(--color-edge)] [overflow-wrap:break-word]">
        {children}
      </code>
    );
  },

  table: ({ children }) => (
    <div
      className={`my-[12px] ${BLOCK} overflow-x-auto rounded-card shadow-[inset_0_0_0_1px_var(--color-edge)]`}
    >
      <table className="w-full min-w-[360px] border-collapse text-[13px] leading-[1.45]">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-ink-100">{children}</thead>,
  th: ({ children }) => (
    <th className="px-[11px] py-[8px] text-left font-semibold text-hi">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-t border-t-edge px-[11px] py-[8px] align-top text-body">{children}</td>
  ),

  img: ({ src, alt }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={typeof src === "string" ? src : undefined}
      alt={alt ?? ""}
      loading="lazy"
      referrerPolicy="no-referrer"
      className={`my-[12px] ${BLOCK} block max-w-full rounded-card`}
    />
  ),
};

/**
 * Render assistant markdown. Memoized on `text` because the live bubble
 * re-renders on every token and committed bubbles must not re-parse with it.
 */
const Markdown = memo(function Markdown({ text }: { text: string }) {
  const safe = useMemo(() => closeOpenMarkdown(text), [text]);
  return (
    // min-w-0 lets the scrollable code/table children actually shrink inside the
    // bubble's flex row instead of stretching it.
    <div className="min-w-0 flex-1 text-[14.5px] leading-[1.55] text-body">
      {/* No rehype-raw, by design — see the header note. */}
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
        {safe}
      </ReactMarkdown>
    </div>
  );
});

export default Markdown;

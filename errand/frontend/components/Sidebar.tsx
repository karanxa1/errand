"use client";

/* Sidebar — the conversation rail. Lists the user's chats (title + relative
   time), a "New chat" action, per-row select + delete (with a two-step inline
   confirm, no modal), and the signed-in identity + sign-out pinned at the
   bottom. On-brand: tonal surfaces, a self-colored lip, a bespoke stroke mark
   for "new". No slop pills, no icon-in-a-tile, content visible by default. */

import { useState } from "react";
import type { Conversation } from "@/lib/useChat";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const s = Math.max(0, Math.floor(diff / 1000));
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.floor(d / 7);
  if (w < 5) return `${w}w ago`;
  return new Date(then).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export default function Sidebar({
  conversations,
  activeId,
  userEmail,
  loading,
  onSelect,
  onNew,
  onDelete,
  onLogout,
  onCloseMobile,
}: {
  conversations: Conversation[];
  activeId: string | null;
  userEmail: string;
  loading: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
  // Optional: dismiss the drawer after a selection on narrow screens.
  onCloseMobile?: () => void;
}) {
  // Which row is showing its delete confirm (two-step, no modal).
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const pick = (id: string) => {
    onSelect(id);
    onCloseMobile?.();
  };

  return (
    <aside className="flex h-full min-h-0 flex-col gap-3 bg-ink-050 px-3 py-[14px] shadow-[inset_-1px_0_0_var(--color-edge)]">
      <button
        className="inline-flex h-[42px] flex-none items-center justify-start gap-[9px] rounded-chip border-none bg-green px-[15px] text-[14px] [font-weight:640] text-on-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.22)] transition-[background-color] duration-[180ms] ease-[ease] hover:bg-green-soft"
        type="button"
        onClick={onNew}
      >
        <span className="inline-flex">
          <NewGlyph />
        </span>
        New chat
      </button>

      <div
        className="flex min-h-0 flex-1 flex-col gap-[3px] overflow-y-auto overflow-x-hidden pr-[2px] [scrollbar-color:var(--color-ink-250)_transparent] [scrollbar-width:thin]"
        role="list"
      >
        {loading && conversations.length === 0 ? (
          <p className="m-0 px-2 py-[10px] text-[12.5px] leading-[1.5] text-low">
            Loading your chats…
          </p>
        ) : conversations.length === 0 ? (
          <p className="m-0 px-2 py-[10px] text-[12.5px] leading-[1.5] text-low">
            No conversations yet. Start one to see it here.
          </p>
        ) : (
          conversations.map((c) => {
            const active = c.id === activeId;
            const confirming = confirmId === c.id;
            return (
              <div
                key={c.id}
                role="listitem"
                className={`group relative flex items-stretch gap-1 rounded-[10px] transition-[background-color] duration-[160ms] ease-[ease] hover:bg-ink-100 ${
                  active
                    ? "bg-ink-150 before:absolute before:top-[9px] before:bottom-[9px] before:left-0 before:w-[3px] before:rounded-[0_3px_3px_0] before:bg-green before:content-['']"
                    : ""
                }`}
              >
                <button
                  className="flex min-w-0 flex-1 flex-col items-start gap-[2px] border-none bg-transparent py-[9px] pr-2 pl-[13px] text-left text-body"
                  type="button"
                  onClick={() => pick(c.id)}
                  aria-current={active ? "true" : undefined}
                >
                  <span
                    className={`max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[13.5px] leading-[1.3] ${
                      active ? "text-hi" : "text-body"
                    }`}
                  >
                    {c.title?.trim() || "Untitled errand"}
                  </span>
                  <span className="text-[11px] text-low">
                    {relativeTime(c.updated_at || c.created_at)}
                  </span>
                </button>

                {confirming ? (
                  <div className="mr-1 inline-flex flex-none gap-1 self-center">
                    <button
                      className="rounded-[7px] border-none bg-danger-dim px-[9px] py-[6px] text-[11.5px] font-semibold text-[#ffe6e1] transition-[background-color] duration-[160ms] ease-[ease] hover:bg-[#7e4740]"
                      type="button"
                      onClick={() => {
                        onDelete(c.id);
                        setConfirmId(null);
                      }}
                      aria-label={`Delete "${c.title || "Untitled errand"}"`}
                    >
                      Delete
                    </button>
                    <button
                      className="rounded-[7px] border-none bg-ink-200 px-[9px] py-[6px] text-[11.5px] font-semibold text-mid transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-250 hover:text-hi"
                      type="button"
                      onClick={() => setConfirmId(null)}
                      aria-label="Keep conversation"
                    >
                      Keep
                    </button>
                  </div>
                ) : (
                  <button
                    className="mr-1 inline-flex h-[30px] w-[30px] flex-none items-center justify-center self-center rounded-[8px] border-none bg-transparent text-low opacity-0 transition-[opacity,background-color,color] duration-[160ms] ease-[ease] group-hover:opacity-100 focus-visible:opacity-100 hover:bg-ink-200 hover:text-danger"
                    type="button"
                    onClick={() => setConfirmId(c.id)}
                    aria-label={`Delete "${c.title || "Untitled errand"}"`}
                    title="Delete conversation"
                  >
                    <TrashGlyph />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>

      <div className="flex flex-none items-center gap-[10px] px-[6px] pt-3 pb-1 shadow-[inset_0_1px_0_var(--color-edge)]">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span
            className="h-[7px] w-[7px] flex-none rounded-full bg-green"
            aria-hidden="true"
          />
          <span
            className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[12.5px] text-mid"
            title={userEmail}
          >
            {userEmail}
          </span>
        </div>
        <button
          className="flex-none rounded-[8px] border-none bg-transparent px-[9px] py-[6px] text-[12.5px] [font-weight:550] text-low transition-[background-color,color] duration-[160ms] ease-[ease] hover:bg-ink-150 hover:text-hi"
          type="button"
          onClick={onLogout}
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}

// A "new" mark in the shared stroke language — a dispatch spark, not a plain +.
function NewGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

// A trash mark drawn in-stroke (round caps), not an icon-pack glyph.
function TrashGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
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

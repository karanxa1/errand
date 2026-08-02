"use client";

/* Sidebar — the conversation rail. Lists the user's chats (title + relative
   time), a "New chat" action, per-row select + delete (with a two-step inline
   confirm, no modal), and the signed-in identity + sign-out pinned at the
   bottom. On-brand: tonal surfaces, a self-colored lip, a bespoke stroke mark
   for "new". No slop pills, no icon-in-a-tile, content visible by default. */

import { useState } from "react";
import type { Conversation } from "@/lib/useChat";
import styles from "./Sidebar.module.css";

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
    <aside className={styles.sidebar}>
      <button className={styles.newBtn} type="button" onClick={onNew}>
        <span className={styles.newMark}>
          <NewGlyph />
        </span>
        New chat
      </button>

      <div className={styles.list} role="list">
        {loading && conversations.length === 0 ? (
          <p className={styles.empty}>Loading your chats…</p>
        ) : conversations.length === 0 ? (
          <p className={styles.empty}>
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
                className={`${styles.item} ${active ? styles.itemActive : ""}`}
              >
                <button
                  className={styles.itemMain}
                  type="button"
                  onClick={() => pick(c.id)}
                  aria-current={active ? "true" : undefined}
                >
                  <span className={styles.itemTitle}>
                    {c.title?.trim() || "Untitled errand"}
                  </span>
                  <span className={styles.itemTime}>
                    {relativeTime(c.updated_at || c.created_at)}
                  </span>
                </button>

                {confirming ? (
                  <div className={styles.confirm}>
                    <button
                      className={styles.confirmYes}
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
                      className={styles.confirmNo}
                      type="button"
                      onClick={() => setConfirmId(null)}
                      aria-label="Keep conversation"
                    >
                      Keep
                    </button>
                  </div>
                ) : (
                  <button
                    className={styles.del}
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

      <div className={styles.foot}>
        <div className={styles.who}>
          <span className={styles.whoDot} aria-hidden="true" />
          <span className={styles.whoEmail} title={userEmail}>
            {userEmail}
          </span>
        </div>
        <button className={styles.logout} type="button" onClick={onLogout}>
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

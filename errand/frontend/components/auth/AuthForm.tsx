"use client";

/* AuthForm — the shared sign-in / create-account form, used by /login and
   /register. On-brand: warm green-black surface with a self-colored lip (no
   drawn border, no glow), a Gambarino headline, real labels above every field
   with genuine contrast, and one solid green primary action (no glowy pill).
   Content is fully present on mount — nothing is gated behind an animation. */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { ErrandMark } from "@/components/Marks";

type Mode = "login" | "register";

// One field shape for every input: dark well, self-colored lip, and a focus
// state that firms the lip and lays a thin accent ring around it.
const INPUT =
  "w-full bg-ink-000 border-0 shadow-[inset_0_0_0_1px_var(--color-edge)] rounded-chip " +
  "text-hi font-body text-[15px] leading-[1.4] px-3.5 py-3 " +
  "transition-[box-shadow,background-color] duration-[180ms] ease-[ease] " +
  "placeholder:text-low focus:outline-none focus:bg-ink-050 " +
  "focus:shadow-[inset_0_0_0_1px_var(--color-edge-strong),0_0_0_3px_var(--color-green-glow)] " +
  "disabled:opacity-60";

const LABEL =
  "text-[11px] tracking-[0.09em] uppercase text-mid font-semibold";

const FIELD = "flex flex-col gap-[7px]";

export default function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const { login, register } = useAuth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isRegister = mode === "register";

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);

    // Client-side guards that mirror the backend contract, so the operator gets
    // an immediate, legible reason before a round-trip.
    const mail = email.trim();
    if (!mail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(mail)) {
      setError("Enter a valid email address.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setBusy(true);
    try {
      if (isRegister) {
        await register(mail, password, name.trim() || undefined);
      } else {
        await login(mail, password);
      }
      router.replace("/");
    } catch (err) {
      setError((err as Error).message || "Something went wrong. Try again.");
      setBusy(false);
    }
  };

  return (
    <main className="relative z-[1] min-h-[100dvh] flex items-center justify-center py-8 px-[clamp(18px,5vw,40px)]">
      <div className="w-full max-w-[420px] bg-[linear-gradient(180deg,var(--color-ink-100),var(--color-ink-050))] rounded-panel shadow-[inset_0_1px_0_rgba(160,240,200,0.08),inset_0_0_0_1px_var(--color-edge),0_26px_60px_-30px_rgba(0,0,0,0.8)] p-[clamp(26px,5vw,38px)]">
        <div className="inline-flex items-center gap-[11px] text-hi mb-[26px]">
          {/* Bare mark — no tile behind it; the drawn mark carries its own weight. */}
          <span className="text-green inline-flex">
            <ErrandMark size={30} />
          </span>
          <span className="font-display text-[22px] tracking-[0.02em] text-hi">
            Errand
          </span>
        </div>

        {/* Tonal emphasis on the accented word — a lighter green step, never a
            saturated pop or a gradient. */}
        <h1 className="font-display text-[clamp(26px,5vw,34px)] leading-[1.12] text-hi mb-2.5 tracking-[0.01em] [&_em]:italic [&_em]:text-green-soft">
          {isRegister ? (
            <>
              Create your <em>Errand</em>.
            </>
          ) : (
            <>
              Welcome <em>back</em>.
            </>
          )}
        </h1>
        <p className="text-mid text-sm leading-[1.55] mb-[26px]">
          {isRegister
            ? "One account keeps your chats, carts, and approvals in one place."
            : "Sign in to pick up your conversations and pending approvals."}
        </p>

        <form className="flex flex-col gap-[15px]" onSubmit={onSubmit} noValidate>
          {isRegister && (
            <label className={FIELD}>
              <span className={LABEL}>Name</span>
              <input
                className={INPUT}
                type="text"
                name="name"
                autoComplete="name"
                placeholder="Optional"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={busy}
              />
            </label>
          )}

          <label className={FIELD}>
            <span className={LABEL}>Email</span>
            <input
              className={INPUT}
              type="email"
              name="email"
              autoComplete="email"
              inputMode="email"
              placeholder="you@work.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={busy}
              required
            />
          </label>

          <label className={FIELD}>
            <span className={LABEL}>Password</span>
            <input
              className={INPUT}
              type="password"
              name="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              placeholder={isRegister ? "At least 8 characters" : "Your password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
              required
              minLength={8}
            />
          </label>

          {error && (
            <p className="mt-0.5 text-[13px] leading-[1.45] text-danger" role="alert">
              {error}
            </p>
          )}

          {/* Primary action — solid green, dark legible label, custom up-right
              dispatch arrow. A clean tonal shift on hover; no lift, no glow. */}
          <button
            className="mt-1.5 h-12 border-0 rounded-chip bg-green text-on-accent [font-weight:650] text-[15px] inline-flex items-center justify-center gap-[9px] shadow-[inset_0_1px_0_rgba(255,255,255,0.22)] transition-[background-color] duration-[180ms] ease-[ease] [&:hover:not(:disabled)]:bg-green-soft disabled:bg-ink-200 disabled:text-low disabled:shadow-[inset_0_0_0_1px_var(--color-edge)] disabled:cursor-default"
            type="submit"
            disabled={busy}
          >
            {busy
              ? isRegister
                ? "Creating account…"
                : "Signing in…"
              : isRegister
                ? "Create account"
                : "Sign in"}
            <svg
              className="transition-transform duration-200 ease-[ease] [button:hover:not(:disabled)_&]:translate-x-0.5 [button:hover:not(:disabled)_&]:-translate-y-0.5"
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M4 12L12 4M12 4H6M12 4V10"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </form>

        <p className="mt-[22px] text-[13.5px] text-mid text-center">
          {isRegister ? (
            <>
              Already have an account?{" "}
              <Link
                className="text-green-soft no-underline font-semibold hover:text-green hover:underline hover:underline-offset-2"
                href="/login"
              >
                Sign in
              </Link>
            </>
          ) : (
            <>
              New to Errand?{" "}
              <Link
                className="text-green-soft no-underline font-semibold hover:text-green hover:underline hover:underline-offset-2"
                href="/register"
              >
                Create an account
              </Link>
            </>
          )}
        </p>
      </div>
    </main>
  );
}

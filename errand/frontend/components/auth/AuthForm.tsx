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
import styles from "./AuthForm.module.css";

type Mode = "login" | "register";

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
    <main className={styles.page}>
      <div className={styles.panel}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <ErrandMark size={30} />
          </span>
          <span className={styles.brandName}>Errand</span>
        </div>

        <h1 className={styles.title}>
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
        <p className={styles.lede}>
          {isRegister
            ? "One account keeps your chats, carts, and approvals in one place."
            : "Sign in to pick up your conversations and pending approvals."}
        </p>

        <form className={styles.form} onSubmit={onSubmit} noValidate>
          {isRegister && (
            <label className={styles.field}>
              <span className={styles.label}>Name</span>
              <input
                className={styles.input}
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

          <label className={styles.field}>
            <span className={styles.label}>Email</span>
            <input
              className={styles.input}
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

          <label className={styles.field}>
            <span className={styles.label}>Password</span>
            <input
              className={styles.input}
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
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}

          <button className={styles.submit} type="submit" disabled={busy}>
            {busy
              ? isRegister
                ? "Creating account…"
                : "Signing in…"
              : isRegister
                ? "Create account"
                : "Sign in"}
            <svg
              className={styles.submitArrow}
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

        <p className={styles.switch}>
          {isRegister ? (
            <>
              Already have an account?{" "}
              <Link className={styles.switchLink} href="/login">
                Sign in
              </Link>
            </>
          ) : (
            <>
              New to Errand?{" "}
              <Link className={styles.switchLink} href="/register">
                Create an account
              </Link>
            </>
          )}
        </p>
      </div>
    </main>
  );
}

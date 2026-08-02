// deviceProfile — the browser's stable identity for the card network, plus the
// two capability checks that decide whether the passkey step can succeed here.
//
// WHY THE ID MUST NEVER BE REGENERATED: the browser profile id is what the card
// network uses to recognise this browser as a device it has already seen. A
// fresh value reads as a brand-new device, which forces another passkey
// registration and consumes one of a hard-capped number of device bindings on
// the token. Mint it per checkout and a card walks straight into "Maximum
// binding for token exceeded" — permanently, for every operator sharing that
// card. So: generated ONCE per browser, persisted, and reused forever after.
//
// The capability checks exist because the two ways this step fails in the field
// (an embedded webview with no platform authenticator, and a hardened browser
// that blocks the cross-origin frame's storage) both fail SILENTLY. The iframe
// paints, the operator clicks, and nothing happens. Naming the cause up front is
// the difference between a ten-second fix and a support ticket.

"use client";

const STORAGE_KEY = "prava_bpid";

// Last-resort identity when localStorage is unavailable or throws (Safari
// private mode raises on setItem even though the API is present). Stable for
// the lifetime of the tab, which is at least stable for the lifetime of one
// checkout — worse than persisted, far better than a new id per request.
let memoryProfileId = "";

function mintProfileId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replace(/-/g, "");
  // randomUUID needs a secure context. getRandomValues does not, and is enough:
  // 16 bytes of CSPRNG output rendered as hex is the same 32 characters. (Same
  // fallback as lib/chatShell.newConversationId.)
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// The stable per-browser id, minted on first call and persisted. Returns "" on
// the server: there is no browser to identify during SSR, and inventing one
// there would be exactly the per-request churn this module exists to prevent.
export function getBrowserProfileId(): string {
  if (typeof window === "undefined") return "";

  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
    const minted = memoryProfileId || mintProfileId();
    window.localStorage.setItem(STORAGE_KEY, minted);
    memoryProfileId = minted;
    return minted;
  } catch {
    // Storage is blocked (private mode, hardened profile, quota). Fall back to
    // the in-memory value so at least every request in this tab agrees.
    if (!memoryProfileId) memoryProfileId = mintProfileId();
    return memoryProfileId;
  }
}

export type PasskeyCapability = { ok: boolean; reason?: string };

// Embedded webviews: Electron and VS Code's Simple Browser, plus Android's
// WebView (which tags itself "; wv" or " wv)" in the UA). None of them expose a
// platform authenticator, so the passkey prompt never appears — the frame just
// sits there.
const EMBEDDED_WEBVIEW = /Electron\/|(?:^|\s)Code\/| wv\)|; wv/;

// Can this browser actually complete the passkey? Never throws: a capability
// probe that blows up is strictly worse than one that reports "unknown", and
// this is called from a render path that must not be interrupted.
export async function checkPasskeyCapability(): Promise<PasskeyCapability> {
  try {
    if (typeof navigator !== "undefined" && EMBEDDED_WEBVIEW.test(navigator.userAgent)) {
      return {
        ok: false,
        reason:
          "This page is running inside an embedded webview, which has no passkey support. Open it in Safari or Chrome to complete the card verification.",
      };
    }

    const available =
      typeof window !== "undefined" &&
      window.PublicKeyCredential?.isUserVerifyingPlatformAuthenticatorAvailable;
    if (typeof available !== "function") {
      return { ok: false, reason: "This browser has no platform authenticator, so the passkey step cannot run here." };
    }

    const ok = await available.call(window.PublicKeyCredential);
    if (!ok) {
      return {
        ok: false,
        reason:
          "No platform authenticator is enrolled on this device. Set up Face ID, Touch ID or Windows Hello, or approve from a device that has one.",
      };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: (err as Error).message };
  }
}

// Can the cross-origin Prava frame reach its own storage? Verification runs in
// an iframe on a different origin, so a browser that partitions or blocks
// third-party storage silently starves it.
export async function checkStorageAccess(): Promise<{ ok: boolean; reason?: string }> {
  try {
    if (typeof document === "undefined" || typeof document.hasStorageAccess !== "function") {
      // The API is absent, which means we cannot tell — and a warning we cannot
      // substantiate trains operators to ignore the ones that are real.
      return { ok: true };
    }
    const granted = await document.hasStorageAccess();
    if (!granted) {
      return {
        ok: false,
        reason:
          "The card network's verification runs in a cross-origin iframe that needs third-party cookies and storage, and this browser is withholding them. Allow third-party cookies for this site, or use a normal window (not private, not a hardened profile).",
      };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: (err as Error).message };
  }
}

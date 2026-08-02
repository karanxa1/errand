"use client";

/* VoiceOrb — the signature artifact.
   A hand-built canvas "voice organism": a directionally-lit core with a liquid
   membrane whose lobes are modulated by REAL microphone frequency bands (Web
   Audio), plus a live waveform ribbon and drifting spark motes. It breathes on
   its own RAF when idle and comes alive with audio when listening.

   Deliberate anti-slop choices:
   - The glow is the orb's OWN body (radial shading offset to a light source at
     upper-left with real falloff) — there is NO separate concentric halo ring
     parked behind it.
   - Motion is authored and continuous; content is never hidden behind it.
   - Colour is the brand green in tonal steps, over the warm green-black. */

import { useEffect, useRef } from "react";

interface Props {
  level: number; // 0..1 overall amplitude
  band: Float32Array; // per-band amplitudes 0..1
  active: boolean; // mic is live
  phase?: "idle" | "listening" | "thinking" | "working" | "done" | "error";
  size?: number;
}

export default function VoiceOrb({
  level,
  band,
  active,
  phase = "idle",
  size = 300,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const levelRef = useRef(level);
  const bandRef = useRef(band);
  const activeRef = useRef(active);
  const phaseRef = useRef(phase);
  levelRef.current = level;
  bandRef.current = band;
  activeRef.current = active;
  phaseRef.current = phase;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx2d = canvas.getContext("2d");
    if (!ctx2d) return;
    const ctx: CanvasRenderingContext2D = ctx2d;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const px = size * dpr;
    canvas.width = px;
    canvas.height = px;
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;

    let raf = 0;
    let t = 0;
    const reduce = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const LOBES = 5;

    // Phase → accent tint. Kept in the green family with a warm shift for done
    // and a red shift for error, so the orb reads state without extra chrome.
    function tint(): { core: string; mid: string; rim: string } {
      const p = phaseRef.current;
      if (p === "error")
        return { core: "#ffd1c9", mid: "#ff7a6b", rim: "#6e3a34" };
      if (p === "done")
        return { core: "#eafff5", mid: "#5ff2b6", rim: "#2b6b52" };
      return { core: "#eafff5", mid: "#13ef93", rim: "#1c6b4d" };
    }

    function draw() {
      t += reduce ? 0 : 1;
      const cx = px / 2;
      const cy = px / 2;
      const lvl = levelRef.current;
      const bands = bandRef.current;
      const live = activeRef.current;
      const { core, mid, rim } = tint();

      ctx.clearRect(0, 0, px, px);

      // Idle breathing baseline; audio adds energy on top.
      const breathe = reduce ? 0 : (Math.sin(t * 0.018) + 1) / 2; // 0..1
      const energy = live ? Math.min(1, lvl * 1.6) : 0;
      const base = px * 0.26;
      const baseR = base * (1 + breathe * 0.03 + energy * 0.12);

      const rot = reduce ? 0 : t * 0.004;

      // ── Membrane: a closed liquid blob whose radius is modulated per-lobe by
      // the audio bands. This is the organism's outer body. ──────────────────
      const pts = 96;
      ctx.beginPath();
      for (let i = 0; i <= pts; i++) {
        const a = (i / pts) * Math.PI * 2;
        // Blend the 5 bands smoothly around the ring.
        const bf = (a / (Math.PI * 2)) * LOBES;
        const bi = Math.floor(bf) % LOBES;
        const bn = (bi + 1) % LOBES;
        const frac = bf - Math.floor(bf);
        const bandAmp =
          (bands[bi] ?? 0) * (1 - frac) + (bands[bn] ?? 0) * frac;
        const wobble = reduce
          ? 0
          : Math.sin(a * 3 + t * 0.03) * 0.012 +
            Math.sin(a * 5 - t * 0.021) * 0.008;
        const r =
          baseR *
          (1 +
            wobble +
            (live ? bandAmp * 0.22 : 0) +
            energy * 0.03);
        const x = cx + Math.cos(a + rot) * r;
        const y = cy + Math.sin(a + rot) * r;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();

      // Body fill: radial gradient offset toward an upper-left light source →
      // the orb glows from its own surface (directional), not a halo behind it.
      const lightX = cx - baseR * 0.42;
      const lightY = cy - baseR * 0.5;
      const g = ctx.createRadialGradient(
        lightX,
        lightY,
        baseR * 0.05,
        cx,
        cy,
        baseR * 1.15,
      );
      const a1 = 0.9;
      g.addColorStop(0, withAlpha(core, 0.95));
      g.addColorStop(0.28, withAlpha(mid, a1));
      g.addColorStop(0.72, withAlpha(mid, 0.42 + energy * 0.25));
      g.addColorStop(1, withAlpha(rim, 0.14));
      ctx.fillStyle = g;
      ctx.fill();

      // Rim lip: a self-colored thin edge catching the same light.
      ctx.lineWidth = Math.max(1, dpr);
      ctx.strokeStyle = withAlpha(core, 0.35 + energy * 0.35);
      ctx.stroke();

      // ── Inner core: a brighter offset nucleus, again lit upper-left. ───────
      const coreR = baseR * (0.42 + energy * 0.1 + breathe * 0.02);
      const cg = ctx.createRadialGradient(
        lightX,
        lightY,
        0,
        lightX,
        lightY,
        coreR * 1.6,
      );
      cg.addColorStop(0, withAlpha(core, 0.9));
      cg.addColorStop(0.5, withAlpha(mid, 0.5));
      cg.addColorStop(1, withAlpha(mid, 0));
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
      ctx.fillStyle = cg;
      ctx.fill();

      // ── Live waveform ribbon across the core (only while listening). ───────
      if (live) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, baseR * 0.98, 0, Math.PI * 2);
        ctx.clip(); // keep the ribbon inside the body — clear cut
        ctx.beginPath();
        const span = baseR * 1.7;
        for (let i = 0; i <= 64; i++) {
          const fx = cx - span / 2 + (span * i) / 64;
          const bi = Math.floor((i / 64) * LOBES) % LOBES;
          const amp = (bands[bi] ?? 0) * baseR * 0.5;
          const fy =
            cy +
            Math.sin(i * 0.5 + t * 0.12) * amp * (0.6 + lvl) +
            Math.sin(i * 0.9 - t * 0.08) * amp * 0.3;
          if (i === 0) ctx.moveTo(fx, fy);
          else ctx.lineTo(fx, fy);
        }
        ctx.strokeStyle = withAlpha(core, 0.5 + lvl * 0.4);
        ctx.lineWidth = Math.max(1.5, dpr * 1.5);
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.stroke();
        ctx.restore();
      }

      // ── Spark motes orbiting, energised by level. ──────────────────────────
      if (!reduce) {
        const motes = 7;
        for (let i = 0; i < motes; i++) {
          const speed = 0.006 + i * 0.0015;
          const a = t * speed + (i / motes) * Math.PI * 2;
          const orbit = baseR * (1.12 + (i % 3) * 0.06) + energy * 10 * dpr;
          const mx = cx + Math.cos(a) * orbit;
          const my = cy + Math.sin(a) * orbit;
          const mr = (0.9 + (i % 2)) * dpr * (1 + energy);
          ctx.beginPath();
          ctx.arc(mx, my, mr, 0, Math.PI * 2);
          ctx.fillStyle = withAlpha(mid, 0.35 + energy * 0.4);
          ctx.fill();
        }
      }

      raf = requestAnimationFrame(draw);
    }

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{ display: "block", filter: "saturate(1.05)" }}
    />
  );
}

function withAlpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, a))})`;
}

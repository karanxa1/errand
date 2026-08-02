// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useVoiceAgent } from "./useVoiceAgent";

class FakeNode {
  connect() {}
  disconnect() {}
}

class FakeAudioContext {
  currentTime = 0;
  destination = new FakeNode();
  state: AudioContextState = "running";
  createMediaStreamSource() { return new FakeNode(); }
  createAnalyser() {
    return Object.assign(new FakeNode(), {
      fftSize: 0,
      smoothingTimeConstant: 0,
      frequencyBinCount: 32,
      getByteFrequencyData: () => {},
    });
  }
  createScriptProcessor() {
    return Object.assign(new FakeNode(), { onaudioprocess: null });
  }
  createGain() {
    return Object.assign(new FakeNode(), { gain: { value: 1 } });
  }
  createBuffer() {
    return { getChannelData: () => new Float32Array(1), duration: 0 };
  }
  createBufferSource() {
    return Object.assign(new FakeNode(), { buffer: null, start: () => {} });
  }
  resume = vi.fn(async () => {});
  close = vi.fn(async () => { this.state = "closed"; });
}

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  // The relay signals auth failure with close code 4401, so the fake must be
  // able to carry a CloseEvent, not just fire.
  onclose: ((event?: CloseEvent) => void) | null = null;
  close = vi.fn(() => { this.readyState = 3; });
  send = vi.fn();

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
}

// The relay is authenticated with a one-shot ticket minted over HTTP, so every
// start() now makes a POST before it may open a socket. Tests stub that mint;
// `mintCalls` is what proves the order (ticket first, socket second).
let mintCalls: { url: string; init?: RequestInit }[] = [];

// Drain the microtask queue so an in-flight `await` inside start() (the mint,
// then its .json()) reaches its next suspension point.
const flushMicrotasks = () => new Promise((resolve) => setTimeout(resolve, 0));

function stubMint(
  respond: (call: number) => Promise<Response> = async () =>
    ({ ok: true, json: async () => ({ ticket: "t-ok", expires_in: 60 }) }) as Response,
) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    mintCalls.push({ url, init });
    return respond(mintCalls.length);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("useVoiceAgent lifecycle", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    mintCalls = [];
    stubMint();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: FakeAudioContext,
    });
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  it("replaces an existing session before starting another one", async () => {
    const stopFirstTrack = vi.fn();
    const stopSecondTrack = vi.fn();
    const getUserMedia = vi
      .fn()
      .mockResolvedValueOnce({ getTracks: () => [{ stop: stopFirstTrack }] })
      .mockResolvedValueOnce({ getTracks: () => [{ stop: stopSecondTrack }] });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    const { result } = renderHook(() => useVoiceAgent("jwt-token"));

    await act(async () => result.current.start("sol", "business"));
    const firstSocket = FakeWebSocket.instances[0];
    expect(firstSocket).toBeDefined();

    await act(async () => result.current.start("luna", "personal"));

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(firstSocket.close).toHaveBeenCalledOnce();
    expect(stopFirstTrack).toHaveBeenCalledOnce();
    expect(stopSecondTrack).not.toHaveBeenCalled();
  });

  it("does not resurrect a stale session when microphone permission resolves late", async () => {
    const stopFirstTrack = vi.fn();
    const stopSecondTrack = vi.fn();
    let resolveFirst!: (stream: { getTracks: () => { stop: () => void }[] }) => void;
    const firstPermission = new Promise<{ getTracks: () => { stop: () => void }[] }>(
      (resolve) => { resolveFirst = resolve; },
    );
    const getUserMedia = vi
      .fn()
      .mockReturnValueOnce(firstPermission)
      .mockResolvedValueOnce({ getTracks: () => [{ stop: stopSecondTrack }] });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });

    const { result } = renderHook(() => useVoiceAgent("jwt-token"));
    let firstStart!: Promise<void>;
    await act(async () => {
      firstStart = result.current.start("sol", "business");
      // The ticket mint now sits in front of the mic prompt, so let it settle:
      // this regression is about a session parked on getUserMedia, and it only
      // gets there once its ticket is in hand.
      await flushMicrotasks();
      expect(getUserMedia).toHaveBeenCalledTimes(1);
      await result.current.start("luna", "personal");
      resolveFirst({ getTracks: () => [{ stop: stopFirstTrack }] });
      await firstStart;
    });

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(stopFirstTrack).toHaveBeenCalledOnce();
    expect(stopSecondTrack).not.toHaveBeenCalled();
  });
});

describe("useVoiceAgent relay auth", () => {
  const stopTrack = vi.fn();

  beforeEach(() => {
    FakeWebSocket.instances = [];
    mintCalls = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: FakeAudioContext,
    });
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] })) },
    });
  });

  it("mints a bearer-authenticated ticket before opening the socket", async () => {
    stubMint();
    const { result } = renderHook(() => useVoiceAgent("jwt-token"));

    await act(async () => result.current.start("sol", "business"));

    expect(mintCalls).toHaveLength(1);
    expect(mintCalls[0].url).toContain("/api/voice/ticket");
    expect(mintCalls[0].init?.method).toBe("POST");
    expect(
      (mintCalls[0].init?.headers as Record<string, string>).Authorization,
    ).toBe("Bearer jwt-token");
    // The ticket must reach the relay, or the socket is closed 4401.
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain("ticket=t-ok");
  });

  it("opens no socket when the ticket mint fails", async () => {
    stubMint(async () => ({ ok: false, status: 401, json: async () => ({}) }) as Response);
    const { result } = renderHook(() => useVoiceAgent(null));

    await act(async () => result.current.start("sol", "business"));

    expect(mintCalls).toHaveLength(1);
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(result.current.error).toBe("Voice needs you to be signed in.");
    expect(result.current.active).toBe(false);
  });

  it("reports a 4401 close as a sign-in problem, not a dropped connection", async () => {
    stubMint();
    const { result } = renderHook(() => useVoiceAgent("jwt-token"));

    await act(async () => result.current.start("sol", "business"));
    const socket = FakeWebSocket.instances[0];
    await act(async () => {
      socket.onclose?.({ code: 4401 } as CloseEvent);
    });

    expect(result.current.error).toBe("Voice needs you to be signed in.");
    expect(result.current.state.connection).not.toBe("lost");
  });

  it("does not open two sockets when start() is called again during the mint await", async () => {
    let releaseFirstMint!: () => void;
    const firstMint = new Promise<Response>((resolve) => {
      releaseFirstMint = () =>
        resolve({
          ok: true,
          json: async () => ({ ticket: "t-stale", expires_in: 60 }),
        } as Response);
    });
    stubMint((call) =>
      call === 1
        ? firstMint
        : Promise.resolve({
            ok: true,
            json: async () => ({ ticket: "t-fresh", expires_in: 60 }),
          } as Response),
    );

    const { result } = renderHook(() => useVoiceAgent("jwt-token"));
    let firstStart!: Promise<void>;
    await act(async () => {
      firstStart = result.current.start("sol", "business");
      await result.current.start("luna", "personal");
      // The first session's ticket arrives AFTER the second session took over.
      releaseFirstMint();
      await firstStart;
    });

    expect(mintCalls).toHaveLength(2);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain("ticket=t-fresh");
  });
});

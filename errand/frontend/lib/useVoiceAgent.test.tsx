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
  onclose: (() => void) | null = null;
  close = vi.fn(() => { this.readyState = 3; });
  send = vi.fn();

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
}

describe("useVoiceAgent lifecycle", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
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

    const { result } = renderHook(() => useVoiceAgent());

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

    const { result } = renderHook(() => useVoiceAgent());
    let firstStart!: Promise<void>;
    await act(async () => {
      firstStart = result.current.start("sol", "business");
      await result.current.start("luna", "personal");
      resolveFirst({ getTracks: () => [{ stop: stopFirstTrack }] });
      await firstStart;
    });

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(stopFirstTrack).toHaveBeenCalledOnce();
    expect(stopSecondTrack).not.toHaveBeenCalled();
  });
});

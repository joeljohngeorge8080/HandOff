import { describe, expect, it } from "vitest";
import { canSwitchPeer, connectedPeerName, formatBytes, isFinished, shortenPath } from "./rules";
import type { Transfer } from "./types";

describe("canSwitchPeer (ADR-047)", () => {
  it("is blocked during an active transfer and allowed otherwise", () => {
    expect(canSwitchPeer(null)).toBe(true);
    expect(canSwitchPeer({ status: "transferring" } as Transfer)).toBe(false);
  });
});

describe("isFinished", () => {
  it("recognises the three terminal states only", () => {
    for (const s of ["completed", "failed", "partially_completed"]) expect(isFinished(s)).toBe(true);
    for (const s of ["created", "validating", "accepted", "transferring", ""]) expect(isFinished(s)).toBe(false);
  });
});

describe("formatBytes", () => {
  it("formats sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(52_428_800)).toBe("50 MB");
  });
});

describe("shortenPath", () => {
  it("leaves short paths alone and keeps both ends of long ones", () => {
    expect(shortenPath("/home/me/Desktop")).toBe("/home/me/Desktop");
    const long = "/home/someone/Documents/Projects/Client/Deliverables/2026/Final";
    const s = shortenPath(long, 30);
    expect(s).toHaveLength(30);
    expect(s.startsWith("/home/")).toBe(true);
    expect(s.endsWith("Final")).toBe(true);
    expect(s).toContain("…");
  });
  it("never produces something longer than the limit", () => {
    for (const max of [10, 20, 44]) expect(shortenPath("x".repeat(200), max).length).toBeLessThanOrEqual(max);
  });
});

describe("connectedPeerName", () => {
  it("is null unless connected with a device", () => {
    expect(connectedPeerName({ connected: false, device: null })).toBeNull();
    expect(connectedPeerName({ connected: false, device: { device_name: "A" } })).toBeNull(); // offline
    expect(connectedPeerName({ connected: true, device: { device_name: "A" } })).toBe("A");
    expect(connectedPeerName({ connected: true, device: null })).toBeNull();
  });
});

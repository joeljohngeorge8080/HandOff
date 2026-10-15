import { describe, expect, it } from "vitest";
import { MAX_CONNECTED_PEERS, connectedPeerCount, connectedPeerName, formatBytes, isFinished, peerLimitReached, shortenPath } from "./rules";
import type { Peer } from "./types";

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

const dev = (name: string, status = "connected"): Peer => ({ device_id: name, device_name: name, address: null, port: null, status });

describe("several connected devices (ADR-063)", () => {
  it("names one device and counts several", () => {
    const c = (devices: Peer[]) => ({ connected: devices.length > 0, device: devices[0] ?? null, devices });
    expect(connectedPeerName(c([dev("A")]))).toBe("A");
    expect(connectedPeerName(c([dev("A"), dev("B")]))).toBe("2 devices");
    expect(connectedPeerName(c([dev("A"), dev("B"), dev("C")]))).toBe("3 devices");
    expect(connectedPeerCount(c([dev("A"), dev("B")]))).toBe(2);
  });
  it("offline devices are not connected", () => {
    const devices = [dev("A"), dev("B", "offline")];
    expect(connectedPeerName({ connected: true, device: devices[0] ?? null, devices })).toBe("A");
    expect(connectedPeerCount({ connected: true, device: devices[0] ?? null, devices })).toBe(1);
    expect(connectedPeerName({ connected: false, device: devices[1] ?? null, devices: [dev("B", "offline")] })).toBeNull();
  });
  it("the limit is four online devices; offline ones do not count against a new connection", () => {
    expect(MAX_CONNECTED_PEERS).toBe(4);
    const four = ["A", "B", "C", "D"].map((n) => dev(n));
    expect(peerLimitReached({ connected: true, device: four[0] ?? null, devices: four })).toBe(true);
    const three = [...four.slice(0, 3), dev("D", "offline")];
    expect(peerLimitReached({ connected: true, device: three[0] ?? null, devices: three })).toBe(false);
    expect(peerLimitReached({ connected: false, device: null })).toBe(false);
  });
});

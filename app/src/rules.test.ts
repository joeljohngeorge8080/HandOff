import { describe, expect, it } from "vitest";
import { canSend, canSwitchPeer, formatBytes, sendBlockedReason } from "./rules";
import type { Connection, Transfer } from "./types";

const peer = { device_id: "d1", device_name: "Aaron", address: "10.0.0.2", port: 8765, status: "connected" };
const connected: Connection = { connected: true, device: peer };
const disconnected: Connection = { connected: false, device: null };
const active = { transfer_id: "t", status: "transferring" } as Transfer;

describe("Send enabled rule", () => {
  it("is enabled with a selection and a connected peer", () => {
    expect(canSend({ selectedCount: 1, connection: connected, activeTransfer: null })).toBe(true);
  });
  it("is disabled with no selection", () => {
    const ctx = { selectedCount: 0, connection: connected, activeTransfer: null };
    expect(canSend(ctx)).toBe(false);
    expect(sendBlockedReason(ctx)).toMatch(/select/i);
  });
  it("is disabled with no peer", () => {
    const ctx = { selectedCount: 2, connection: disconnected, activeTransfer: null };
    expect(canSend(ctx)).toBe(false);
    expect(sendBlockedReason(ctx)).toMatch(/connect/i);
  });
  it("is disabled when the peer went offline", () => {
    const offline: Connection = { connected: false, device: { ...peer, status: "offline" } };
    expect(canSend({ selectedCount: 1, connection: offline, activeTransfer: null })).toBe(false);
  });
  it("is disabled while a transfer is active", () => {
    const ctx = { selectedCount: 1, connection: connected, activeTransfer: active };
    expect(canSend(ctx)).toBe(false);
    expect(sendBlockedReason(ctx)).toMatch(/progress/i);
  });
  it("has no blocked reason when enabled", () => {
    expect(sendBlockedReason({ selectedCount: 1, connection: connected, activeTransfer: null })).toBe("");
  });
});

describe("peer switching (ADR-047)", () => {
  it("is blocked during an active transfer only", () => {
    expect(canSwitchPeer(active)).toBe(false);
    expect(canSwitchPeer(null)).toBe(true);
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

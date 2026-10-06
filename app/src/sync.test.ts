import { describe, expect, it, vi } from "vitest";
import type { Input } from "./edge/machine";
import { POLL_BUSY_MS, POLL_IDLE_MS, Sync } from "./sync";
import type { Snapshot, Transfer } from "./types";

function tr(over: Partial<Transfer> = {}): Transfer {
  return {
    transfer_id: "tr_1", direction: "sent", peer_device_id: "d", peer_device_name: "Aaron-Laptop",
    file_count: 1, total_size: 10, archive_size: 10, bytes_transferred: 0, status: "transferring",
    error_code: null, error_message: null, created_at: "", completed_at: null,
    files: [{ name: "a.png", size: 10, status: "pending", failure_code: null }], ...over,
  };
}
const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  device: { device_id: "me", device_name: "Me" }, receive_directory: "/home/me/Desktop",
  connection: { connected: true, device: { device_id: "d", device_name: "Aaron-Laptop", address: null, port: null, status: "connected" } },
  active_transfer: null, recent_history: [], ...over,
});

function setup(initial: Snapshot, busy = false) {
  let push: ((e: { payload: unknown }) => void) | null = null;
  const inputs: Input[] = [];
  const notices: string[] = [];
  const names: string[][] = [];
  const timers: { ms: number; fn: () => void }[] = [];
  const core = vi.fn(async () => initial);
  const sync = new Sync({
    core: core as never,
    listen: (async (_e: string, h: (e: { payload: unknown }) => void) => {
      push = h;
      return () => {};
    }) as never,
    dispatch: (i) => inputs.push(i),
    notify: (n) => notices.push(n.body),
    isBusy: () => busy,
    onNames: (n) => names.push(n),
    schedule: (ms, fn) => {
      timers.push({ ms, fn });
      return () => {};
    },
  });
  return { sync, inputs, notices, names, timers, core, push: (p: unknown) => push?.({ payload: p }) };
}

describe("Sync", () => {
  it("starts from a snapshot: peer, busy state and active transfer", async () => {
    const s = setup(snap({ active_transfer: tr() }));
    await s.sync.start();
    expect(s.inputs[0]).toMatchObject({ type: "snapshot", peer: "Aaron-Laptop", active: { transfer_id: "tr_1" } });
    expect(s.names).toEqual([["a.png"]]);
  });
  it("an offline peer is not a connected peer", async () => {
    const s = setup(snap({ connection: { connected: false, device: { device_id: "d", device_name: "A", address: null, port: null, status: "offline" } } }));
    await s.sync.start();
    expect(s.inputs[0]).toMatchObject({ peer: null });
  });
  it("forwards pushed transfer updates and notifies once when one finishes", async () => {
    const s = setup(snap());
    await s.sync.start();
    s.push({ event: "transfer.updated", data: tr({ status: "transferring" }) });
    s.push({ event: "transfer.updated", data: tr({ status: "completed" }) });
    s.push({ event: "transfer.updated", data: tr({ status: "completed" }) });
    expect(s.inputs.filter((i) => i.type === "transfer")).toHaveLength(3);
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0]).toMatch(/Transfer complete/);
  });
  it("does not replay old history as notifications at startup", async () => {
    const s = setup(snap({ recent_history: [tr({ status: "completed" })] }));
    await s.sync.start();
    expect(s.notices).toEqual([]);
  });
  it("forwards connection changes", async () => {
    const s = setup(snap());
    await s.sync.start();
    s.push({ event: "connection.changed", data: { connected: false, device: null } });
    expect(s.inputs.at(-1)).toEqual({ type: "connection", peer: null });
  });
  it("ignores unknown or malformed events", async () => {
    const s = setup(snap());
    await s.sync.start();
    const before = s.inputs.length;
    for (const e of [null, {}, { event: "ready" }, { event: "transfer.updated" }, "x", { event: "connection.changed" }]) s.push(e);
    expect(s.inputs.length).toBe(before);
  });
  it("polls slowly when idle and faster while a transfer runs", async () => {
    const idle = setup(snap(), false);
    await idle.sync.start();
    expect(idle.timers.at(-1)?.ms).toBe(POLL_IDLE_MS);
    const busy = setup(snap(), true);
    await busy.sync.start();
    expect(busy.timers.at(-1)?.ms).toBe(POLL_BUSY_MS);
    expect(POLL_BUSY_MS).toBeLessThan(POLL_IDLE_MS);
  });
  it("keeps going when the core cannot be reached", async () => {
    const s = setup(snap());
    s.core.mockRejectedValue(new Error("gone"));
    await expect(s.sync.start()).resolves.toBeUndefined();
    expect(s.timers).toHaveLength(1);
  });
  it("stops cleanly", async () => {
    const s = setup(snap());
    await s.sync.start();
    s.sync.stop();
    s.timers.at(-1)?.fn();
    await new Promise((r) => setTimeout(r, 0));
    expect(s.timers).toHaveLength(1); // no new tick scheduled after stop
  });
});

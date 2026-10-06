import { describe, expect, it, vi } from "vitest";
import { InteractionBus } from "../interaction/bus";
import type { Transfer } from "../types";
import { EdgeController } from "./controller";
import type { Input } from "./machine";

const tick = () => new Promise((r) => setTimeout(r, 0));

function transfer(over: Partial<Transfer> = {}): Transfer {
  return {
    transfer_id: "tr_1", direction: "sent", peer_device_id: "d", peer_device_name: "Aaron-Laptop",
    file_count: 1, total_size: 10, archive_size: 10, bytes_transferred: 0, status: "created",
    error_code: null, error_message: null, created_at: "", completed_at: null, files: [], ...over,
  };
}

function setup(core: (action: string, payload?: Record<string, unknown>) => Promise<unknown>) {
  const timers: { ms: number; fn: () => void; cancelled: boolean }[] = [];
  const modes: string[] = [];
  const notes: string[] = [];
  const c = new EdgeController({
    core: core as never,
    setWindowMode: async (m) => {
      modes.push(m);
    },
    notify: (_t, b) => notes.push(b),
    timer: (ms, fn) => {
      const t = { ms, fn, cancelled: false };
      timers.push(t);
      return () => {
        t.cancelled = true;
      };
    },
  });
  c.dispatch({ type: "connection", peer: "Aaron-Laptop" });
  return { c, timers, modes, notes };
}
const drop = (paths = ["/a.png"]) => ({ source: "os" as const, type: "release" as const, paths });

describe("EdgeController", () => {
  it("sends a drop through the core and adopts the transfer it returns", async () => {
    const core = vi.fn(async () => ({ transfer: transfer() }));
    const { c, modes } = setup(core);
    c.dispatch(drop());
    await tick();
    expect(core).toHaveBeenCalledWith("drop.send", { paths: ["/a.png"] });
    expect(c.state.view).toMatchObject({ kind: "sending", transferId: "tr_1" });
    expect(modes).toContain("stage");
  });

  it("never reports success from the send reply alone", async () => {
    const { c } = setup(async () => ({ transfer: transfer({ status: "created" }) }));
    c.dispatch(drop());
    await tick();
    expect(c.state.view.kind).toBe("sending");
  });

  it("shows a core refusal with its per-item reasons and notifies once", async () => {
    const core = async () => {
      throw { code: "FILE_TYPE_NOT_SUPPORTED", message: "x", error: { details: { items: [{ name: "a.exe", ok: false, reason: "unsupported_type" }] } } };
    };
    const { c, notes } = setup(core);
    c.dispatch(drop(["/a.exe"]));
    await tick();
    expect(c.state.view).toMatchObject({ kind: "rejected", message: "This file type is not supported" });
    expect(notes).toEqual(["This file type is not supported"]);
  });

  it("treats an unreachable core as a failed transfer, not a hang", async () => {
    const { c } = setup(async () => {
      throw new Error("core gone");
    });
    c.dispatch(drop());
    await tick();
    expect(c.state.view).toMatchObject({ kind: "send_failed", message: "Transfer failed" });
  });

  it("validates on drag enter and shows the verdict", async () => {
    const core = vi.fn(async () => ({ ok: true, file_count: 1, total_size: 3, items: [] }));
    const { c } = setup(core);
    c.dispatch({ source: "os", type: "drag_start", paths: ["/a.png"] });
    await tick();
    expect(core).toHaveBeenCalledWith("drop.inspect", { paths: ["/a.png"] });
    expect(c.state.view.kind).toBe("ready");
  });

  it("an inspection that fails shows a refusal instead of getting stuck validating", async () => {
    const { c } = setup(async () => {
      throw new Error("nope");
    });
    c.dispatch({ source: "os", type: "drag_start", paths: ["/a.png"] });
    await tick();
    expect(c.state.view.kind).toBe("rejected");
  });

  it("returns to idle when the hold timer fires, and cancels pending timers on dispose", async () => {
    const { c, timers, modes } = setup(async () => ({ transfer: transfer() }));
    c.dispatch(drop());
    await tick();
    c.dispatch({ type: "transfer", transfer: transfer({ status: "failed" }) });
    expect(c.state.view.kind).toBe("send_failed");
    timers.at(-1)?.fn();
    expect(c.state.view.kind).toBe("idle");
    expect(modes.at(-1)).toBe("idle");
    c.dispatch({ type: "transfer", transfer: transfer({ transfer_id: "t2", direction: "received", status: "completed" }) });
    c.dispose();
    expect(timers.at(-1)?.cancelled).toBe(true);
  });

  it("survives a window-mode failure", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const c = new EdgeController({
      core: (async () => ({ transfer: transfer() })) as never,
      setWindowMode: async () => {
        throw new Error("no window");
      },
      notify: () => {},
      timer: () => () => {},
    });
    c.dispatch({ type: "connection", peer: "P" });
    c.dispatch(drop());
    await tick();
    expect(c.state.view.kind).toBe("sending");
    err.mockRestore();
  });

  it("is driven by the semantic bus", async () => {
    const { c } = setup(async () => ({ transfer: transfer() }));
    const bus = new InteractionBus();
    c.attach(bus);
    bus.emit({ source: "cv", type: "release", paths: ["/a.png"] });
    await tick();
    expect(c.state.view.kind).toBe("sending");
  });

  it("tells listeners about changes only when the model changes", () => {
    const { c } = setup(async () => ({ transfer: transfer() }));
    const seen: Input[] = [];
    c.onChange(() => seen.push({ type: "close_panel" }));
    c.dispatch({ type: "proximity", phase: "far" });
    expect(seen).toHaveLength(0);
    c.dispatch({ type: "proximity", phase: "near" });
    expect(seen).toHaveLength(1);
  });
});

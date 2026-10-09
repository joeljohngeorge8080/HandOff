import { describe, expect, it } from "vitest";
import {
  type Effect,
  type Input,
  type Model,
  type View,
  initialModel,
  labelFor,
  reduce,
  HOLD_MS,
} from "./machine";
import type { DropInspection, Transfer } from "../types";

const os = { source: "os" } as const;

function run(start: Model, ...inputs: Input[]): { model: Model; effects: Effect[]; all: Effect[][] } {
  let model = start;
  const all: Effect[][] = [];
  for (const i of inputs) {
    const s = reduce(model, i);
    model = s.model;
    all.push(s.effects);
  }
  return { model, effects: all.flat(), all };
}

const withPeer = (name: string | null = "Aaron-Laptop"): Model => ({ ...initialModel(), peer: name });
const kind = (m: Model): string => m.view.kind;
const of = (effects: Effect[], type: Effect["type"]) => effects.filter((e) => e.type === type);

function tr(over: Partial<Transfer> = {}): Transfer {
  return {
    transfer_id: "tr_1", direction: "sent", peer_device_id: "d", peer_device_name: "Aaron-Laptop",
    file_count: 2, total_size: 1000, archive_size: 1000, bytes_transferred: 0, status: "created",
    error_code: null, error_message: null, created_at: "", completed_at: null, files: [], ...over,
  };
}
const inspection = (over: Partial<DropInspection> = {}): DropInspection => ({
  ok: true, file_count: 2, total_size: 10, items: [{ name: "a.png", ok: true }, { name: "b.txt", ok: true }], ...over,
});
const drag = (n = 2) => ({ ...os, type: "drag_start" as const, paths: Array.from({ length: n }, (_, i) => `/f${i}.png`) });
const drop = (n = 2) => ({ ...os, type: "release" as const, paths: Array.from({ length: n }, (_, i) => `/f${i}.png`) });

describe("waking up", () => {
  it("starts idle and stays quiet when the pointer is far", () => {
    const { model, effects } = run(initialModel(), { type: "proximity", phase: "far" });
    expect(kind(model)).toBe("idle");
    expect(effects).toEqual([]);
  });
  it("approach -> armed as a drag gets closer, and a weaker signal never downgrades", () => {
    const { model } = run(withPeer(), { type: "proximity", phase: "near" }, { type: "proximity", phase: "drag" }, { type: "proximity", phase: "near" });
    expect(kind(model)).toBe("armed");
  });
  it("resting at the edge shows the handle; clicking it opens the panel", () => {
    const { model, effects } = run(withPeer(), { type: "proximity", phase: "dwell" }, { ...os, type: "pointer_click", x: 1, y: 0.5 });
    expect(kind(model)).toBe("panel");
    expect(effects).toContainEqual({ type: "window", mode: "panel" });
  });
  it("a click while the edge is idle does nothing", () => {
    const { model } = run(withPeer(), { ...os, type: "pointer_click", x: 1, y: 0.5 });
    expect(kind(model)).toBe("idle");
  });
  it("pressing the button on the handle (which looks like a drag) still lets the click open the panel", () => {
    const { model } = run(
      withPeer(),
      { type: "proximity", phase: "dwell" },
      { type: "proximity", phase: "drag" },
      { ...os, type: "pointer_click", x: 1, y: 0.5 },
    );
    expect(kind(model)).toBe("panel");
  });
  it("a click on an armed strip opens the panel too, a real drag never produces a click", () => {
    const { model } = run(withPeer(), { type: "proximity", phase: "drag" }, { ...os, type: "pointer_click", x: 1, y: 0.5 });
    expect(kind(model)).toBe("panel");
  });
  it("the pointer leaving cancels everything that was only waiting for a drop", () => {
    for (const phase of ["near", "drag", "dwell"] as const) {
      const { model } = run(withPeer(), { type: "proximity", phase }, { type: "proximity", phase: "far" });
      expect(kind(model)).toBe("idle");
    }
    const { model } = run(withPeer(), drag(), { type: "proximity", phase: "far" });
    expect(kind(model)).toBe("idle");
  });
});

describe("a valid drop", () => {
  it("drag enter validates, a good verdict shows Ready to send", () => {
    const a = run(withPeer(), { type: "proximity", phase: "drag" }, drag());
    expect(kind(a.model)).toBe("validating");
    expect(a.effects).toContainEqual({ type: "inspect", paths: ["/f0.png", "/f1.png"] });
    const b = run(a.model, { type: "inspected", result: inspection() });
    expect(kind(b.model)).toBe("ready");
    expect(labelFor(b.model.view, null)).toBe("Ready to send");
  });
  it("release sends exactly once and switches to the animation stage", () => {
    const { model, effects } = run(withPeer(), drag(), { type: "inspected", result: inspection() }, drop());
    expect(kind(model)).toBe("sending");
    expect(of(effects, "send")).toEqual([{ type: "send", paths: ["/f0.png", "/f1.png"] }]);
    expect(effects).toContainEqual({ type: "window", mode: "stage" });
    expect(labelFor(model.view, null)).toBe("Sending to Aaron-Laptop");
  });
  it("release before validation finished still sends (the core re-validates everything)", () => {
    const { model, effects } = run(withPeer(), drag(), drop());
    expect(kind(model)).toBe("sending");
    expect(of(effects, "send")).toHaveLength(1);
  });
  it("release without any earlier drag events still works", () => {
    const { model } = run(withPeer(), drop(1));
    expect(kind(model)).toBe("sending");
  });
  it("several files are one send", () => {
    const { effects } = run(withPeer(), drop(5));
    expect(of(effects, "send")).toHaveLength(1);
    expect((of(effects, "send")[0] as { paths: string[] }).paths).toHaveLength(5);
  });
});

describe("refusals", () => {
  it("shows No HandOff device connected as soon as a drag arrives with no peer", () => {
    const { model } = run(withPeer(null), drag());
    expect(model.view).toMatchObject({ kind: "rejected", reason: "no_peer", message: "No HandOff device connected" });
  });
  it("and dropping then notifies and sends nothing", () => {
    const { model, effects } = run(withPeer(null), drag(), drop());
    expect(of(effects, "send")).toEqual([]);
    expect(of(effects, "notify")).toEqual([{ type: "notify", title: "HandOff", body: "No HandOff device connected" }]);
    expect(model.view).toMatchObject({ kind: "rejected", released: true });
  });
  it("a drop with no earlier drag events and no peer is refused too", () => {
    const { effects } = run(withPeer(null), drop());
    expect(of(effects, "send")).toEqual([]);
    expect(of(effects, "notify")).toHaveLength(1);
  });
  it("refuses while a transfer is in flight", () => {
    const m = { ...withPeer(), busy: true };
    const { model, effects } = run(m, drag(), drop());
    expect(model.view).toMatchObject({ kind: "rejected", reason: "busy" });
    expect(of(effects, "send")).toEqual([]);
  });
  it.each([
    ["directory", "Folders are not supported"],
    ["symlink", "Shortcuts are not supported"],
    ["unsupported_type", "This file type is not supported"],
    ["executable_content", "This file type is not supported"],
    ["too_large", "Files over 50 MB are not supported"],
    ["missing", "That file no longer exists"],
  ])("explains a %s", (reason, message) => {
    const bad = inspection({ ok: false, items: [{ name: "a.png", ok: true }, { name: "x", ok: false, reason }] });
    const { model } = run(withPeer(), drag(), { type: "inspected", result: bad });
    expect(model.view).toMatchObject({ kind: "rejected", message });
  });
  it("dropping something already refused notifies and sends nothing", () => {
    const bad = inspection({ ok: false, items: [{ name: "x.exe", ok: false, reason: "unsupported_type" }] });
    const { effects, model } = run(withPeer(), drag(1), { type: "inspected", result: bad }, drop(1));
    expect(of(effects, "send")).toEqual([]);
    expect(of(effects, "notify")).toEqual([{ type: "notify", title: "HandOff", body: "This file type is not supported" }]);
    expect(of(effects, "schedule")).toHaveLength(1);
    expect(model.view).toMatchObject({ kind: "rejected", released: true });
  });
  it("a refusal made by the core on send is shown the same way", () => {
    const { model, effects } = run(withPeer(), drop(1), {
      type: "send_failed", code: "FILE_TYPE_NOT_SUPPORTED", message: "x",
      items: [{ name: "a.exe", ok: false, reason: "unsupported_type" }],
    });
    expect(model.view).toMatchObject({ kind: "rejected", message: "This file type is not supported" });
    expect(of(effects, "notify")).toHaveLength(1);
  });
  it.each([
    ["DEVICE_NOT_FOUND", "No HandOff device connected"],
    ["INVALID_STATE", "A transfer is already in progress"],
    ["DEVICE_OFFLINE", "Aaron-Laptop is offline"],
  ])("maps core error %s", (code, message) => {
    const { model } = run(withPeer(), drop(1), { type: "send_failed", code, message: "ignored" });
    expect(model.view).toMatchObject({ kind: "rejected", message });
  });
  it("any other send error is a plain failure that returns to idle", () => {
    const a = run(withPeer(), drop(1), { type: "send_failed", code: "INTERNAL_ERROR", message: "boom" });
    expect(a.model.view).toMatchObject({ kind: "send_failed", message: "Transfer failed" });
  });
  it("a stale verdict after the drag left is ignored", () => {
    const { model } = run(withPeer(), drag(), { type: "proximity", phase: "far" }, { type: "inspected", result: inspection() });
    expect(kind(model)).toBe("idle");
  });
  it("cancelling the drag goes back to waiting, not to a stuck state", () => {
    const { model } = run(withPeer(), drag(), { type: "inspected", result: inspection() }, { ...os, type: "drag_end", cancelled: true });
    expect(kind(model)).toBe("armed");
  });
  it("losing the peer mid-drag turns the strip red", () => {
    const { model } = run(withPeer(), drag(), { type: "connection", peer: null });
    expect(model.view).toMatchObject({ kind: "rejected", reason: "no_peer" });
  });
  it("getting a peer while the red no-peer message shows recovers", () => {
    const { model } = run(withPeer(null), drag(), { type: "connection", peer: "Aaron-Laptop" });
    expect(kind(model)).toBe("armed");
  });
});

describe("sending follows the core, never the other way round", () => {
  const sending = () => run(withPeer(), drop(2), { type: "sent", transferId: "tr_1" });
  it("progress follows bytes_transferred", () => {
    const { model } = run(sending().model, { type: "transfer", transfer: tr({ status: "transferring", bytes_transferred: 250 }) });
    expect(model.view).toMatchObject({ kind: "sending", progress: 0.25, transferId: "tr_1" });
  });
  it("progress never reaches 100% before completed", () => {
    const { model } = run(sending().model, { type: "transfer", transfer: tr({ status: "transferring", bytes_transferred: 1000 }) });
    expect((model.view as Extract<View, { kind: "sending" }>).progress).toBeLessThan(1);
  });
  it("is NOT a success until the core says completed", () => {
    for (const status of ["created", "validating", "accepted", "transferring"]) {
      const { model } = run(sending().model, { type: "transfer", transfer: tr({ status }) });
      expect(kind(model)).toBe("sending");
    }
    const { model } = run(sending().model, { type: "transfer", transfer: tr({ status: "completed" }) });
    expect(kind(model)).toBe("send_success");
    expect(labelFor(model.view, null)).toBe("Transfer complete");
  });
  it("failed and partially_completed are failures", () => {
    const a = run(sending().model, { type: "transfer", transfer: tr({ status: "failed" }) });
    expect(a.model.view).toMatchObject({ kind: "send_failed", message: "Transfer failed" });
    const b = run(sending().model, { type: "transfer", transfer: tr({ status: "partially_completed" }) });
    expect(b.model.view).toMatchObject({ kind: "send_failed", message: "Some files failed" });
  });
  it("ignores updates of another transfer", () => {
    const { model } = run(sending().model, { type: "transfer", transfer: tr({ transfer_id: "tr_other", status: "completed" }) });
    expect(kind(model)).toBe("sending");
  });
  it("adopts the transfer id from the first update when the send reply has not arrived", () => {
    const { model } = run(withPeer(), drop(1), { type: "transfer", transfer: tr({ status: "validating" }) });
    expect(model.view).toMatchObject({ kind: "sending", transferId: "tr_1" });
  });
  it("busy follows the core, so the next drop is refused until it finishes", () => {
    const mid = run(withPeer(), drop(1), { type: "transfer", transfer: tr({ status: "transferring" }) });
    expect(mid.model.busy).toBe(true);
    const done = run(mid.model, { type: "transfer", transfer: tr({ status: "completed" }) });
    expect(done.model.busy).toBe(false);
  });
});

describe("returning to idle", () => {
  it("success, failure and refusals all return to idle on their own timer and shrink the window", () => {
    const cases: Input[][] = [
      [drop(1), { type: "transfer", transfer: tr({ status: "completed" }) }],
      [drop(1), { type: "transfer", transfer: tr({ status: "failed" }) }],
      [drop(1), { type: "send_failed", code: "INTERNAL_ERROR", message: "x" }],
      [{ type: "transfer", transfer: tr({ direction: "received", status: "completed" }) }],
    ];
    for (const inputs of cases) {
      const a = run(withPeer(), ...inputs);
      const sched = of(a.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>;
      expect(sched).toBeDefined();
      const b = run(a.model, { type: "timer", token: sched.token });
      expect(kind(b.model)).toBe("idle");
      expect(b.effects).toContainEqual({ type: "window", mode: "idle" });
    }
    const a = run(withPeer(null), drop(1));
    const sched = of(a.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>;
    expect(kind(run(a.model, { type: "timer", token: sched.token }).model)).toBe("idle");
  });
  it("a stale timer cannot interrupt a newer view", () => {
    const a = run(withPeer(), drop(1), { type: "transfer", transfer: tr({ status: "failed" }) });
    const oldToken = (of(a.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>).token;
    const b = run(a.model, { type: "timer", token: oldToken }, drop(1), { type: "sent", transferId: "tr_2" });
    expect(kind(b.model)).toBe("sending");
    const c = run(b.model, { type: "timer", token: oldToken });
    expect(kind(c.model)).toBe("sending");
  });
  it("a timer does nothing while a transfer is still running", () => {
    const { model } = run(withPeer(), drop(1), { type: "timer", token: 1 });
    expect(kind(model)).toBe("sending");
  });
  it("holds long enough to read: failures longest", () => {
    expect(HOLD_MS.failed).toBeGreaterThan(HOLD_MS.success);
    expect(HOLD_MS.rejected).toBeGreaterThanOrEqual(2000);
  });
});

describe("repeated transfers and recovery", () => {
  it("many drops in a row all work, including after failures", () => {
    let model = withPeer();
    const outcomes = ["completed", "failed", "completed", "partially_completed", "completed"];
    outcomes.forEach((status, n) => {
      const id = `tr_${n}`;
      let s = run(model, drop(1), { type: "sent", transferId: id });
      expect(kind(s.model)).toBe("sending");
      s = run(s.model, { type: "transfer", transfer: tr({ transfer_id: id, status: "transferring", bytes_transferred: 10 }) });
      s = run(s.model, { type: "transfer", transfer: tr({ transfer_id: id, status }) });
      const tok = (of(s.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>).token;
      s = run(s.model, { type: "timer", token: tok });
      expect(kind(s.model)).toBe("idle");
      expect(s.model.busy).toBe(false);
      model = s.model;
    });
  });
  it("after a refusal the next valid drop is accepted", () => {
    const a = run(withPeer(null), drop(1));
    const b = run({ ...a.model, view: { kind: "idle" }, peer: "Aaron-Laptop" }, drop(1));
    expect(kind(b.model)).toBe("sending");
  });
});

describe("receiving mirrors sending", () => {
  const rx = (over: Partial<Transfer> = {}) => ({ type: "transfer" as const, transfer: tr({ direction: "received", ...over }) });
  it("an incoming transfer shows receiving with progress, on the stage", () => {
    const { model, effects } = run(withPeer(), rx({ status: "transferring", bytes_transferred: 500, archive_size: null }));
    expect(model.view).toMatchObject({ kind: "receiving", progress: 0.5, peer: "Aaron-Laptop" });
    expect(effects).toContainEqual({ type: "window", mode: "stage" });
    expect(labelFor(model.view, null)).toBe("Receiving from Aaron-Laptop");
  });
  it("success only when the core says completed", () => {
    const a = run(withPeer(), rx({ status: "transferring" }));
    expect(kind(run(a.model, rx({ status: "accepted" })).model)).toBe("receiving");
    expect(kind(run(a.model, rx({ status: "completed" })).model)).toBe("receive_success");
  });
  it("a failed receive is shown as a failure", () => {
    const a = run(withPeer(), rx({ status: "transferring" }), rx({ status: "failed" }));
    expect(a.model.view).toMatchObject({ kind: "receive_failed", message: "Transfer failed" });
  });
  it("a receive that finished before we ever saw it in flight still shows its result", () => {
    const { model, effects } = run(withPeer(), rx({ status: "completed" }));
    expect(kind(model)).toBe("receive_success");
    expect(effects).toContainEqual({ type: "window", mode: "stage" });
  });
  it("does not hijack the panel", () => {
    const { model } = run(withPeer(), { type: "open_panel" }, rx({ status: "transferring" }));
    expect(kind(model)).toBe("panel");
    expect(model.busy).toBe(true);
  });
  it("does not hijack a drag in progress", () => {
    const { model } = run(withPeer(), drag(), rx({ status: "transferring" }));
    expect(kind(model)).toBe("validating");
  });
});

describe("panel", () => {
  it("opens from idle and closes back to a click-through hairline", () => {
    const a = run(withPeer(), { type: "open_panel" });
    expect(a.effects).toContainEqual({ type: "window", mode: "panel" });
    const b = run(a.model, { type: "close_panel" });
    expect(kind(b.model)).toBe("idle");
    expect(b.effects).toContainEqual({ type: "window", mode: "idle" });
  });
  it("cannot be opened over an animation", () => {
    const { model } = run(withPeer(), drop(1), { type: "open_panel" });
    expect(kind(model)).toBe("sending");
  });
  it("ignores drag events while open", () => {
    const { model } = run(withPeer(), { type: "open_panel" }, drag());
    expect(kind(model)).toBe("panel");
  });
});

describe("snapshots", () => {
  it("restore peer and busy state, and adopt a transfer already running", () => {
    const { model } = run(initialModel(), { type: "snapshot", peer: "Aaron-Laptop", active: tr({ status: "transferring", bytes_transferred: 100 }) });
    expect(model.peer).toBe("Aaron-Laptop");
    expect(model.busy).toBe(true);
    expect(kind(model)).toBe("sending");
  });
  it("an idle snapshot clears a stale busy flag", () => {
    const { model } = run({ ...withPeer(), busy: true }, { type: "snapshot", peer: "Aaron-Laptop", active: null });
    expect(model.busy).toBe(false);
  });
});

describe("computer-vision readiness", () => {
  it("ignores the reserved canonical events without changing state", () => {
    const m = withPeer();
    const reserved: Input[] = [
      { ...os, type: "pointer_move", x: 0.5, y: 0.5 },
      { ...os, type: "pointer_down", x: 0.5, y: 0.5 },
      { ...os, type: "pointer_up", x: 0.5, y: 0.5 },
      { ...os, type: "selection_changed", paths: [] },
      { ...os, type: "drag_move", x: 0.1, y: 0.1 },
      { ...os, type: "grab", paths: [] },
      { source: "cv", type: "gesture_detected", gesture: "fist", confidence: 0.9 },
      { source: "cv", type: "direction_detected", direction: "right" },
    ];
    for (const e of reserved) {
      const s = reduce(m, e);
      expect(s.model).toEqual(m);
      expect(s.effects).toEqual([]);
    }
  });
  it("drives the same behaviour from a CV-sourced drag and release", () => {
    const cv = (type: "drag_start" | "release") => ({ source: "cv" as const, type, paths: ["/a.png"] });
    const { model, effects } = run(withPeer(), cv("drag_start"), cv("release"));
    expect(kind(model)).toBe("sending");
    expect(of(effects, "send")).toHaveLength(1);
  });
});

describe("hand grab and release animations", () => {
  const cv = (gesture: string): Input => ({ source: "cv", type: "gesture_detected", gesture, confidence: 1 });

  it("an open-to-closed palm shows the grab view, a closed-to-open palm the release view", () => {
    expect(kind(run(withPeer(), cv("palm_grab")).model)).toBe("hand_grab");
    expect(kind(run(withPeer(), cv("palm_release")).model)).toBe("hand_release");
  });
  it("wakes the strip, then returns to idle on its own timer and shrinks the window", () => {
    const a = run(withPeer(), cv("palm_grab"));
    expect(a.effects).toContainEqual({ type: "window", mode: "stage" });
    const sched = of(a.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>;
    expect(sched.ms).toBe(HOLD_MS.hand);
    const b = run(a.model, { type: "timer", token: sched.token });
    expect(kind(b.model)).toBe("idle");
    expect(b.effects).toContainEqual({ type: "window", mode: "idle" });
  });
  it("never interrupts a transfer, a drag or a panel", () => {
    for (const start of [run(withPeer(), drop(1)).model, run(withPeer(), drag()).model, run(withPeer(), { type: "open_panel" }).model]) {
      const s = reduce(start, cv("palm_release"));
      expect(s.model.view).toEqual(start.view);
      expect(s.effects).toEqual([]);
    }
  });
  it("only a CV-sourced gesture can trigger it", () => {
    const s = reduce(withPeer(), { source: "os", type: "gesture_detected", gesture: "palm_grab", confidence: 1 } as Input);
    expect(kind(s.model)).toBe("idle");
  });
  it("other and unknown gestures still change nothing", () => {
    for (const g of ["fist", "pinch_closed", "copied", "sent", "send_failed", "palm_grab_x", ""]) {
      expect(kind(run(withPeer(), cv(g)).model)).toBe("idle");
    }
  });
  it("the transfer that a release starts takes over from the release view", () => {
    const { model } = run(withPeer(), cv("palm_release"), { type: "transfer", transfer: tr({ status: "created" }) });
    expect(kind(model)).toBe("sending");
  });
  it("a stale timer from the grab cannot cut a later view short", () => {
    const a = run(withPeer(), cv("palm_grab"));
    const old = (of(a.effects, "schedule").at(-1) as Extract<Effect, { type: "schedule" }>).token;
    const b = run(a.model, { type: "transfer", transfer: tr({ status: "created" }) }, { type: "timer", token: old });
    expect(kind(b.model)).toBe("sending");
  });
  it("shows a short label for each", () => {
    expect(labelFor({ kind: "hand_grab", token: 1 }, null)).toBe("Grabbed");
    expect(labelFor({ kind: "hand_release", token: 1 }, null)).toBe("Released");
  });
});

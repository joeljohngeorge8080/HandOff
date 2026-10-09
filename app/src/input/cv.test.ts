import { describe, expect, it } from "vitest";
import { InteractionBus } from "../interaction/bus";
import type { InteractionEvent } from "../interaction/events";
import type { HandControl } from "../types";
import { attachCv, handControlLabel } from "./cv";

function setup() {
  let handler: ((e: { payload: unknown }) => void) | null = null;
  const bus = new InteractionBus();
  const events: InteractionEvent[] = [];
  const statuses: HandControl[] = [];
  bus.subscribe((e) => events.push(e));
  const listen = (async (name: string, h: (e: { payload: unknown }) => void) => {
    expect(name).toBe("core-event");
    handler = h;
    return () => {};
  }) as never;
  void attachCv(listen, bus, (s) => statuses.push(s));
  return { events, statuses, fire: (payload: unknown) => handler?.({ payload }) };
}

describe("hand control adapter", () => {
  it("turns gesture_detected and direction_detected into cv-sourced bus events", async () => {
    const t = setup();
    await Promise.resolve();
    t.fire({ event: "cv.event", data: { event: "gesture_detected", gesture: "pinch_closed", confidence: 1 } });
    t.fire({ event: "cv.event", data: { event: "direction_detected", direction: "right" } });
    expect(t.events).toEqual([
      { source: "cv", type: "gesture_detected", gesture: "pinch_closed", confidence: 1 },
      { source: "cv", type: "direction_detected", direction: "right" },
    ]);
  });

  it("never forwards events that could start or end a drop", async () => {
    const t = setup();
    await Promise.resolve();
    for (const event of ["release", "grab", "drag_start", "drag_move", "drag_end", "pointer_click", "pointer_down"]) {
      t.fire({ event: "cv.event", data: { event, paths: ["/etc/passwd"], x: 1, y: 1 } });
    }
    expect(t.events).toEqual([]);
  });

  it("ignores malformed payloads and other core events", async () => {
    const t = setup();
    await Promise.resolve();
    for (const p of [
      null, undefined, 5, {}, { event: "cv.event" }, { event: "cv.event", data: "x" },
      { event: "cv.event", data: { event: "direction_detected", direction: "sideways" } },
      { event: "cv.event", data: { event: "gesture_detected", gesture: 7 } },
      { event: "transfer.updated", data: { event: "gesture_detected", gesture: "x" } },
      { event: "cv.status", data: { state: "exploding" } },
    ]) {
      t.fire(p);
    }
    expect(t.events).toEqual([]);
    expect(t.statuses).toEqual([]);
  });

  it("passes status through", async () => {
    const t = setup();
    await Promise.resolve();
    t.fire({ event: "cv.status", data: { state: "error", message: "No camera", code: "CV_CAMERA_UNAVAILABLE" } });
    t.fire({ event: "cv.status", data: { state: "tracking" } });
    expect(t.statuses).toEqual([
      { state: "error", message: "No camera", code: "CV_CAMERA_UNAVAILABLE" },
      { state: "tracking", message: "", code: undefined },
    ]);
  });
});

describe("handControlLabel", () => {
  it("says the camera is unused when off, whatever a stale status says", () => {
    expect(handControlLabel(false, { state: "tracking", message: "" })).toMatch(/not in use/);
  });
  it("describes each state", () => {
    expect(handControlLabel(true, null)).toMatch(/Starting/);
    expect(handControlLabel(true, { state: "tracking", message: "" })).toMatch(/Pinch/);
    expect(handControlLabel(true, { state: "no_hand", message: "" })).toMatch(/Hold your hand/);
    expect(handControlLabel(true, { state: "error", message: "Needs an X11 session" })).toBe("Needs an X11 session");
    expect(handControlLabel(true, { state: "error", message: "" })).toMatch(/unavailable/);
  });
});

import { describe, expect, it } from "vitest";
import { InteractionBus } from "../interaction/bus";
import type { InteractionEvent } from "../interaction/events";
import { CANONICAL_EVENTS } from "../interaction/events";
import { type DragPayload, attachOsDrag } from "./osDrag";
import { attachProximity } from "./proximity";
import type { Input } from "../edge/machine";

function fakeDrag() {
  let handler: ((e: { payload: DragPayload }) => void) | null = null;
  let detached = false;
  return {
    source: {
      onDragDropEvent: async (h: (e: { payload: DragPayload }) => void) => {
        handler = h;
        return () => {
          detached = true;
        };
      },
    },
    fire: (payload: DragPayload) => handler?.({ payload }),
    get detached() {
      return detached;
    },
  };
}
const record = (bus: InteractionBus) => {
  const seen: InteractionEvent[] = [];
  bus.subscribe((e) => seen.push(e));
  return seen;
};

describe("OS drag-and-drop adapter", () => {
  const viewport = () => ({ width: 200, height: 1000 });

  it("turns enter / over / leave into drag_start / drag_move / drag_end", async () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    const d = fakeDrag();
    await attachOsDrag(d.source, bus, viewport);
    d.fire({ type: "enter", paths: ["/a.png", "/b.txt"], position: { x: 100, y: 500 } });
    d.fire({ type: "over", position: { x: 50, y: 250 } });
    d.fire({ type: "leave" });
    expect(seen).toEqual([
      { source: "os", type: "drag_start", paths: ["/a.png", "/b.txt"] },
      { source: "os", type: "drag_move", x: 0.5, y: 0.5 },
      { source: "os", type: "drag_move", x: 0.25, y: 0.25 },
      { source: "os", type: "drag_end", cancelled: true },
    ]);
  });

  it("turns a drop into release followed by a completed drag_end", async () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    const d = fakeDrag();
    await attachOsDrag(d.source, bus, viewport);
    d.fire({ type: "drop", paths: ["/a.png"], position: { x: 10, y: 10 } });
    expect(seen).toEqual([
      { source: "os", type: "release", paths: ["/a.png"] },
      { source: "os", type: "drag_end", cancelled: false },
    ]);
  });

  it("clamps positions outside the window and survives nonsense", async () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    const d = fakeDrag();
    await attachOsDrag(d.source, bus, viewport);
    d.fire({ type: "over", position: { x: -50, y: 99999 } });
    d.fire({ type: "over", position: { x: Number.NaN, y: Number.POSITIVE_INFINITY } });
    expect(seen[0]).toMatchObject({ x: 0, y: 1 });
    expect(seen[1]).toMatchObject({ x: 0, y: 0 });
  });

  it("copes with a drag that carries no path list", async () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    const d = fakeDrag();
    await attachOsDrag(d.source, bus, viewport);
    d.fire({ type: "enter", position: { x: 1, y: 1 } } as unknown as DragPayload);
    expect(seen[0]).toEqual({ source: "os", type: "drag_start", paths: [] });
  });

  it("only ever emits canonical ADR-052 event names", async () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    const d = fakeDrag();
    await attachOsDrag(d.source, bus, viewport);
    d.fire({ type: "enter", paths: ["/a"], position: { x: 0, y: 0 } });
    d.fire({ type: "over", position: { x: 0, y: 0 } });
    d.fire({ type: "drop", paths: ["/a"], position: { x: 0, y: 0 } });
    d.fire({ type: "leave" });
    for (const e of seen) expect(CANONICAL_EVENTS).toContain(e.type);
    expect(new Set(seen.map((e) => e.source))).toEqual(new Set(["os"]));
  });
});

describe("a failing listener", () => {
  it("never stops the others", () => {
    const bus = new InteractionBus();
    const seen = record(bus);
    bus.subscribe(() => {
      throw new Error("boom");
    });
    bus.emit({ source: "os", type: "drag_end", cancelled: true });
    expect(seen).toHaveLength(1);
  });
  it("unsubscribes", () => {
    const bus = new InteractionBus();
    const seen: InteractionEvent[] = [];
    const off = bus.subscribe((e) => seen.push(e));
    off();
    bus.emit({ source: "os", type: "drag_end", cancelled: true });
    expect(seen).toEqual([]);
  });
});

describe("proximity adapter", () => {
  function setup() {
    let handler: ((e: { payload: unknown }) => void) | null = null;
    const listen = (async (_ev: string, h: (e: { payload: unknown }) => void) => {
      handler = h;
      return () => {};
    }) as never;
    const inputs: Input[] = [];
    const bus = new InteractionBus();
    const events = record(bus);
    return { listen, inputs, bus, events, fire: (p: unknown) => handler?.({ payload: p }) };
  }
  it("forwards each phase", async () => {
    const s = setup();
    await attachProximity(s.listen, (i) => s.inputs.push(i), s.bus);
    for (const phase of ["near", "drag", "dwell", "far"]) s.fire({ phase });
    expect(s.inputs.map((i) => (i as { phase: string }).phase)).toEqual(["near", "drag", "dwell", "far"]);
  });
  it("announces a drag toward the edge as direction_detected right", async () => {
    const s = setup();
    await attachProximity(s.listen, (i) => s.inputs.push(i), s.bus);
    s.fire({ phase: "drag" });
    expect(s.events).toEqual([{ source: "os", type: "direction_detected", direction: "right" }]);
  });
  it("ignores unknown phases and malformed payloads", async () => {
    const s = setup();
    await attachProximity(s.listen, (i) => s.inputs.push(i), s.bus);
    for (const p of [{ phase: "sideways" }, {}, null, undefined, { phase: 7 }]) s.fire(p);
    expect(s.inputs).toEqual([]);
  });
});

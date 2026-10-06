// Input adapter: the native edge watcher -> machine input.
//
// The Rust side watches the pointer (a dragged file cannot be seen by a click-through window)
// and reports how close a drag is. A drag heading for the edge is also announced as the
// canonical `direction_detected: right`, which is what a hand-tracking input would report.
import type { InteractionBus } from "../interaction/bus";
import type { Input, Proximity } from "../edge/machine";

export type Listen = <T>(event: string, handler: (e: { payload: T }) => void) => Promise<() => void>;

const PHASES: readonly string[] = ["far", "near", "drag", "dwell"];

export function attachProximity(
  listen: Listen,
  dispatch: (input: Input) => void,
  bus: InteractionBus,
): Promise<() => void> {
  return listen<{ phase?: string }>("edge-proximity", ({ payload }) => {
    const phase = payload?.phase;
    if (typeof phase !== "string" || !PHASES.includes(phase)) return;
    dispatch({ type: "proximity", phase: phase as Proximity });
    if (phase === "drag") bus.emit({ source: "os", type: "direction_detected", direction: "right" });
  });
}

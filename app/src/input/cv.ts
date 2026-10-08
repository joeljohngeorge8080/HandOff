// Input adapter: hand control (ADR-056) -> status for the panel and feedback events for the bus.
//
// The camera never names a file and never starts a transfer: a hand-held drag reaches the app as
// a real OS drag (see osDrag.ts). This adapter therefore forwards only the two canonical events
// that are pure feedback, with source "cv". `release`, `grab` and `drag_*` are deliberately NOT
// forwarded: a second `release` without paths would make the state machine report an invalid drop.
// The core already validates the payload; it is checked again here because it crosses a process
// boundary.
import type { InteractionBus } from "../interaction/bus";
import type { Listen } from "./proximity";
import type { HandControl } from "../types";

const STATES: readonly string[] = ["off", "starting", "tracking", "no_hand", "error"];
const DIRECTIONS = ["left", "right", "up", "down"] as const;

interface CoreEvent {
  event?: string;
  data?: Record<string, unknown>;
}

export function attachCv(
  listen: Listen,
  bus: InteractionBus,
  onStatus: (status: HandControl) => void,
): Promise<() => void> {
  return listen<CoreEvent>("core-event", ({ payload }) => {
    const d = payload?.data;
    if (!d || typeof d !== "object") return;
    if (payload.event === "cv.status") {
      if (typeof d.state === "string" && STATES.includes(d.state)) {
        onStatus({
          state: d.state as HandControl["state"],
          message: typeof d.message === "string" ? d.message : "",
          code: typeof d.code === "string" ? d.code : undefined,
        });
      }
    } else if (payload.event === "cv.event") {
      if (d.event === "gesture_detected" && typeof d.gesture === "string") {
        const confidence = typeof d.confidence === "number" ? d.confidence : 0;
        bus.emit({ source: "cv", type: "gesture_detected", gesture: d.gesture, confidence });
      } else if (d.event === "direction_detected" && DIRECTIONS.includes(d.direction as never)) {
        bus.emit({ source: "cv", type: "direction_detected", direction: d.direction as (typeof DIRECTIONS)[number] });
      }
    }
  });
}

/** One line for the panel under the Hand control switch. */
export function handControlLabel(enabled: boolean, h: HandControl | null): string {
  if (!enabled) return "Off. The camera is not in use.";
  switch (h?.state) {
    case "tracking":
      return "Camera on. Pinch to grab, open to let go.";
    case "no_hand":
      return "Camera on. Hold your hand up to the camera.";
    case "error":
      return h.message || "Hand control is unavailable.";
    case "starting":
    default:
      return "Starting the camera…";
  }
}

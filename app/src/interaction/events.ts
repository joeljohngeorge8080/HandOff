// The semantic interaction vocabulary (ADR-052). Input adapters translate raw input (OS
// drag-and-drop today, hand tracking in a later phase) into these events; the edge state
// machine and the animations only ever see these, never a mouse or a camera.
//
// Coordinates are normalised to the edge window: x and y are 0..1, (0,0) top-left.

export type InputSource = "os" | "cv";

export interface Point {
  x: number;
  y: number;
}

export type InteractionEvent = { source: InputSource } & (
  | ({ type: "pointer_move" } & Point)
  | ({ type: "pointer_click" } & Point)
  | ({ type: "pointer_down" } & Point)
  | ({ type: "pointer_up" } & Point)
  | { type: "selection_changed"; paths: string[] }
  | { type: "drag_start"; paths: string[] }
  | ({ type: "drag_move" } & Point)
  | { type: "drag_end"; cancelled: boolean }
  | { type: "grab"; paths: string[] }
  | { type: "release"; paths: string[] }
  | { type: "gesture_detected"; gesture: string; confidence: number }
  | { type: "direction_detected"; direction: "left" | "right" | "up" | "down" }
);

export type InteractionEventType = InteractionEvent["type"];

/** The canonical names, in the order ADR-052 lists them. */
export const CANONICAL_EVENTS: readonly InteractionEventType[] = [
  "pointer_move",
  "pointer_click",
  "pointer_down",
  "pointer_up",
  "selection_changed",
  "drag_start",
  "drag_move",
  "drag_end",
  "grab",
  "release",
  "gesture_detected",
  "direction_detected",
];

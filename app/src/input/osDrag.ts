// Input adapter: native OS drag-and-drop -> canonical semantic events.
//
// This is the only module that knows about Tauri's drag-drop payloads. Files dragged from the
// Desktop, Explorer or a Linux file manager arrive here as absolute paths; they are *claims*
// only, and the core re-validates every path before touching it.
import type { InteractionBus } from "../interaction/bus";

export type DragPayload =
  | { type: "enter"; paths: string[]; position: { x: number; y: number } }
  | { type: "over"; position: { x: number; y: number } }
  | { type: "drop"; paths: string[]; position: { x: number; y: number } }
  | { type: "leave" };

export interface DragDropSource {
  onDragDropEvent(handler: (event: { payload: DragPayload }) => void): Promise<() => void>;
}

export interface Viewport {
  /** Size of the window in physical pixels (drag positions are physical). */
  width: number;
  height: number;
}

const clamp01 = (n: number): number => (Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);

export function attachOsDrag(
  source: DragDropSource,
  bus: InteractionBus,
  viewport: () => Viewport,
): Promise<() => void> {
  const norm = (p: { x: number; y: number }) => {
    const v = viewport();
    return { x: clamp01(p.x / (v.width || 1)), y: clamp01(p.y / (v.height || 1)) };
  };
  return source.onDragDropEvent(({ payload }) => {
    switch (payload.type) {
      case "enter":
        bus.emit({ source: "os", type: "drag_start", paths: [...(payload.paths ?? [])] });
        bus.emit({ source: "os", type: "drag_move", ...norm(payload.position) });
        break;
      case "over":
        bus.emit({ source: "os", type: "drag_move", ...norm(payload.position) });
        break;
      case "drop":
        bus.emit({ source: "os", type: "release", paths: [...(payload.paths ?? [])] });
        bus.emit({ source: "os", type: "drag_end", cancelled: false });
        break;
      case "leave":
        bus.emit({ source: "os", type: "drag_end", cancelled: true });
        break;
    }
  });
}

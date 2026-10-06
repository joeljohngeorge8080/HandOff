// A minimal typed event bus between the input layer and the edge controller.
import type { InteractionEvent } from "./events";

export type Listener = (event: InteractionEvent) => void;

export class InteractionBus {
  private listeners = new Set<Listener>();

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(event: InteractionEvent): void {
    for (const l of [...this.listeners]) {
      try {
        l(event);
      } catch (e) {
        // One broken listener must not stop the others (or the input adapter).
        console.error("interaction listener failed", e);
      }
    }
  }
}

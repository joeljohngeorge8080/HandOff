// Runs the machine: feeds it inputs and carries out its effects.
//
// This is the only edge module with side effects. It talks to the core only through the IPC
// bridge, never to peers or the filesystem (CLAUDE.md non-negotiable #9).
import type { InteractionBus } from "../interaction/bus";
import type { DropInspection, DropItem, Transfer } from "../types";
import {
  type Effect,
  type Input,
  type Model,
  type WindowMode,
  initialModel,
  reduce,
} from "./machine";

export interface ControllerDeps {
  /** Calls a core action (`core_request`). Rejects with an object that has `code`/`message`. */
  core: <T>(action: string, payload?: Record<string, unknown>) => Promise<T>;
  setWindowMode: (mode: WindowMode) => Promise<void>;
  notify: (title: string, body: string) => void;
  /** Starts a timer; returns a cancel function. */
  timer: (ms: number, fn: () => void) => () => void;
}

interface ErrorLike {
  code?: string;
  message?: string;
  error?: { details?: { items?: DropItem[] } };
}

const itemsOf = (e: unknown): DropItem[] | undefined => {
  const items = (e as ErrorLike | null)?.error?.details?.items;
  return Array.isArray(items) ? items : undefined;
};

export type ModelListener = (model: Model, previous: Model) => void;

export class EdgeController {
  private model: Model = initialModel();
  private listeners = new Set<ModelListener>();
  private cancelTimers = new Set<() => void>();

  constructor(private readonly deps: ControllerDeps) {}

  get state(): Model {
    return this.model;
  }

  onChange(listener: ModelListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Connect the semantic-event bus to the machine. */
  attach(bus: InteractionBus): () => void {
    return bus.subscribe((event) => this.dispatch(event));
  }

  dispatch(input: Input): void {
    const previous = this.model;
    const { model, effects } = reduce(previous, input);
    this.model = model;
    for (const effect of effects) this.run(effect);
    if (model !== previous) for (const l of [...this.listeners]) l(model, previous);
  }

  dispose(): void {
    for (const cancel of this.cancelTimers) cancel();
    this.cancelTimers.clear();
  }

  private run(effect: Effect): void {
    switch (effect.type) {
      case "window":
        void this.deps.setWindowMode(effect.mode).catch((e) => console.error("window mode failed", e));
        break;
      case "notify":
        this.deps.notify(effect.title, effect.body);
        break;
      case "schedule": {
        const cancel = this.deps.timer(effect.ms, () => {
          this.cancelTimers.delete(cancel);
          this.dispatch({ type: "timer", token: effect.token });
        });
        this.cancelTimers.add(cancel);
        break;
      }
      case "inspect":
        void this.inspect(effect.paths);
        break;
      case "send":
        void this.send(effect.paths);
        break;
    }
  }

  private async inspect(paths: string[]): Promise<void> {
    try {
      const result = await this.deps.core<DropInspection>("drop.inspect", { paths });
      this.dispatch({ type: "inspected", result });
    } catch (e) {
      this.dispatch({ type: "inspect_failed", message: (e as ErrorLike)?.message ?? "Could not check these files" });
    }
  }

  private async send(paths: string[]): Promise<void> {
    try {
      const res = await this.deps.core<{ transfer: Transfer }>("drop.send", { paths });
      this.dispatch({ type: "sent", transferId: res.transfer.transfer_id });
      this.dispatch({ type: "transfer", transfer: res.transfer });
    } catch (e) {
      const err = e as ErrorLike;
      this.dispatch({
        type: "send_failed",
        code: err?.code ?? "INTERNAL_ERROR",
        message: err?.message ?? "Transfer failed",
        items: itemsOf(e),
      });
    }
  }
}

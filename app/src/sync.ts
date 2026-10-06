// Keeps the UI in step with the core: pushed events first, a slow snapshot as a safety net.
//
// The core pushes `transfer.updated` and `connection.changed`. If an event is ever missed the
// next snapshot corrects the picture; while a transfer is running it is polled a little faster.
import type { Input } from "./edge/machine";
import { TransferWatcher, type Notice } from "./notifications";
import { connectedPeerName } from "./rules";
import type { Connection, Snapshot, Transfer } from "./types";

export const POLL_IDLE_MS = 15_000;
export const POLL_BUSY_MS = 1_500;

export interface SyncDeps {
  core: <T>(action: string, payload?: Record<string, unknown>) => Promise<T>;
  listen: <T>(event: string, handler: (e: { payload: T }) => void) => Promise<() => void>;
  dispatch: (input: Input) => void;
  notify: (notice: Notice) => void;
  isBusy: () => boolean;
  /** Names of the files in a transfer, for the animation tokens. */
  onNames?: (names: string[]) => void;
  schedule?: (ms: number, fn: () => void) => () => void;
}

interface CoreEvent {
  event?: string;
  data?: unknown;
}

export class Sync {
  private readonly watcher = new TransferWatcher();
  private stopPoll: (() => void) | null = null;
  private stopped = false;
  private offs: (() => void)[] = [];

  constructor(private readonly deps: SyncDeps) {}

  async start(): Promise<void> {
    this.offs.push(await this.deps.listen<CoreEvent>("core-event", ({ payload }) => this.onEvent(payload)));
    await this.refresh();
    this.loop();
  }

  stop(): void {
    this.stopped = true;
    this.stopPoll?.();
    for (const off of this.offs) off();
  }

  /** Pull a snapshot and apply it (also used right after startup). */
  async refresh(): Promise<Snapshot | null> {
    try {
      const snap = await this.deps.core<Snapshot>("status.snapshot");
      this.apply(snap);
      return snap;
    } catch {
      return null; // the core is unreachable; the next tick tries again
    }
  }

  private apply(snap: Snapshot): void {
    const all = snap.active_transfer ? [snap.active_transfer, ...snap.recent_history] : snap.recent_history;
    for (const n of this.watcher.update(all)) this.deps.notify(n);
    this.deps.dispatch({ type: "snapshot", peer: connectedPeerName(snap.connection), active: snap.active_transfer });
    if (snap.active_transfer) this.names(snap.active_transfer);
  }

  private names(t: Transfer): void {
    this.deps.onNames?.(t.files.map((f) => f.name));
  }

  private onEvent(e: CoreEvent): void {
    if (e?.event === "transfer.updated" && e.data) {
      const t = e.data as Transfer;
      this.names(t);
      this.deps.dispatch({ type: "transfer", transfer: t });
      for (const n of this.watcher.update([t])) this.deps.notify(n);
    } else if (e?.event === "connection.changed" && e.data) {
      this.deps.dispatch({ type: "connection", peer: connectedPeerName(e.data as Connection) });
    }
  }

  private loop(): void {
    if (this.stopped) return;
    const schedule = this.deps.schedule ?? ((ms, fn) => {
      const id = setTimeout(fn, ms);
      return () => clearTimeout(id);
    });
    this.stopPoll = schedule(this.deps.isBusy() ? POLL_BUSY_MS : POLL_IDLE_MS, () => {
      void this.refresh().finally(() => this.loop());
    });
  }
}

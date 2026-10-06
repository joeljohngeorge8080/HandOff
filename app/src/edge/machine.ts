// The edge interaction as an explicit, deterministic state machine (ADR-054).
//
// `reduce(model, input)` is pure: it returns the next model plus a list of effects for the
// controller to run (inspect, send, resize the window, notify, set a timer). No DOM, no Tauri,
// no mouse: inputs are the canonical semantic events (ADR-052) plus facts reported by the core.
// A future computer-vision adapter feeds the same inputs and gets the same behaviour.
//
// Honesty rule: the *_success views are reachable only from a transfer the core reports as
// completed. Nothing here ever guesses that a transfer worked.

import type { InteractionEvent } from "../interaction/events";
import type { DropInspection, DropItem, Transfer } from "../types";
import { FINISHED_STATES } from "../types";
import {
  MESSAGES,
  type RejectReason,
  rejectionFor,
  rejectionForError,
  sendingTo,
} from "./messages";

export type Proximity = "far" | "near" | "drag" | "dwell";
export type WindowMode = "idle" | "armed" | "panel" | "stage";

export type View =
  | { kind: "idle" }
  | { kind: "approach" }
  | { kind: "armed" }
  | { kind: "handle" }
  | { kind: "validating"; count: number }
  | { kind: "ready"; count: number; totalSize: number }
  | { kind: "rejected"; reason: RejectReason; message: string; released: boolean; token: number }
  | { kind: "sending"; transferId: string | null; progress: number; peer: string; count: number }
  | { kind: "send_success"; peer: string; count: number; token: number }
  | { kind: "send_failed"; message: string; token: number }
  | { kind: "receiving"; transferId: string; progress: number; peer: string; count: number }
  | { kind: "receive_success"; peer: string; count: number; token: number }
  | { kind: "receive_failed"; message: string; token: number }
  | { kind: "panel" };

export type ViewKind = View["kind"];

export interface Model {
  view: View;
  /** Name of the connected, online peer; null when there is none. */
  peer: string | null;
  /** The core has a transfer in flight (either direction). */
  busy: boolean;
  /** Counter that gives every terminal view its own timer token. */
  seq: number;
}

export type Input =
  | InteractionEvent
  | { type: "proximity"; phase: Proximity }
  | { type: "inspected"; result: DropInspection }
  | { type: "inspect_failed"; message: string }
  | { type: "sent"; transferId: string }
  | { type: "send_failed"; code: string; message: string; items?: DropItem[] }
  | { type: "transfer"; transfer: Transfer }
  | { type: "connection"; peer: string | null }
  | { type: "snapshot"; peer: string | null; active: Transfer | null }
  | { type: "open_panel" }
  | { type: "close_panel" }
  | { type: "timer"; token: number };

export type Effect =
  | { type: "inspect"; paths: string[] }
  | { type: "send"; paths: string[] }
  | { type: "window"; mode: WindowMode }
  | { type: "notify"; title: string; body: string }
  | { type: "schedule"; ms: number; token: number };

export interface Step {
  model: Model;
  effects: Effect[];
}

export const HOLD_MS = { rejected: 2200, success: 2600, failed: 3600 } as const;

export const initialModel = (): Model => ({ view: { kind: "idle" }, peer: null, busy: false, seq: 0 });

const PRE_DRAG: ReadonlySet<ViewKind> = new Set(["idle", "approach", "armed", "handle"]);
const DRAGGING: ReadonlySet<ViewKind> = new Set(["validating", "ready"]);
const TERMINAL_VIEWS: ReadonlySet<ViewKind> = new Set([
  "rejected",
  "send_success",
  "send_failed",
  "receive_success",
  "receive_failed",
]);

/** Views in which nothing has been sent yet: a drag can still come, go or be refused. */
const preSend = (v: View): boolean =>
  PRE_DRAG.has(v.kind) || DRAGGING.has(v.kind) || (v.kind === "rejected" && !v.released);

export const isFinished = (status: string): boolean => (FINISHED_STATES as readonly string[]).includes(status);

const progressOf = (t: Transfer): number => {
  const total = t.archive_size ?? t.total_size;
  if (!total) return 0;
  return Math.max(0, Math.min(0.99, t.bytes_transferred / total));
};

const step = (model: Model, effects: Effect[] = []): Step => ({ model, effects });
const stay = (model: Model): Step => step(model);

function withView(m: Model, view: View, effects: Effect[] = []): Step {
  return step({ ...m, view }, effects);
}

/** Enter a view that is shown for a while and then returns to idle. */
function hold(
  m: Model,
  make: (token: number) => View,
  ms: number,
  effects: Effect[] = [],
): Step {
  const token = m.seq + 1;
  return step({ ...m, seq: token, view: make(token) }, [...effects, { type: "schedule", ms, token }]);
}

const stage: Effect = { type: "window", mode: "stage" };

function reject(m: Model, reason: RejectReason, message: string, released: boolean, extra: Effect[] = []): Step {
  if (!released) {
    return step({ ...m, view: { kind: "rejected", reason, message, released: false, token: m.seq } }, extra);
  }
  return hold(m, (token) => ({ kind: "rejected", reason, message, released: true, token }), HOLD_MS.rejected, [
    ...extra,
    { type: "notify", title: "HandOff", body: message },
  ]);
}

export function reduce(m: Model, input: Input): Step {
  switch (input.type) {
    case "proximity":
      return onProximity(m, input.phase);
    case "drag_start":
      return onDragStart(m, input.paths);
    case "drag_end":
      return onDragEnd(m, input.cancelled);
    case "release":
      return onRelease(m, input.paths);
    case "pointer_click":
      return m.view.kind === "handle" ? openPanel(m) : stay(m);
    case "inspected":
      return onInspected(m, input.result);
    case "inspect_failed":
      return m.view.kind === "validating" ? reject(m, "invalid", input.message, false) : stay(m);
    case "sent":
      return m.view.kind === "sending" && m.view.transferId === null
        ? withView(m, { ...m.view, transferId: input.transferId })
        : stay(m);
    case "send_failed":
      return onSendFailed(m, input);
    case "transfer":
      return onTransfer(m, input.transfer);
    case "connection":
      return onConnection(m, input.peer);
    case "snapshot":
      return onSnapshot(m, input.peer, input.active);
    case "open_panel":
      return openPanel(m);
    case "close_panel":
      return m.view.kind === "panel" ? withView(m, { kind: "idle" }, [{ type: "window", mode: "idle" }]) : stay(m);
    case "timer":
      return onTimer(m, input.token);
    // Reserved for the computer-vision phase and for pointer detail the view reads directly.
    default:
      return stay(m);
  }
}

function openPanel(m: Model): Step {
  if (!PRE_DRAG.has(m.view.kind)) return stay(m);
  return withView(m, { kind: "panel" }, [{ type: "window", mode: "panel" }]);
}

function onProximity(m: Model, phase: Proximity): Step {
  const k = m.view.kind;
  if (phase === "far") {
    // The pointer left: anything that was only waiting for a drop is over.
    return preSend(m.view) && k !== "idle" ? withView(m, { kind: "idle" }) : stay(m);
  }
  if (!PRE_DRAG.has(k)) return stay(m);
  const next: ViewKind = phase === "near" ? "approach" : phase === "drag" ? "armed" : "handle";
  // A weaker signal never downgrades a stronger one (e.g. "near" while already armed).
  const rank: Record<string, number> = { idle: 0, approach: 1, handle: 2, armed: 3 };
  if (phase === "near" && (rank[k] ?? 0) > (rank.approach ?? 1)) return stay(m);
  return k === next ? stay(m) : withView(m, { kind: next } as View);
}

function onDragStart(m: Model, paths: string[]): Step {
  if (!preSend(m.view)) return stay(m);
  if (m.peer === null) return reject(m, "no_peer", MESSAGES.noPeer, false);
  if (m.busy) return reject(m, "busy", MESSAGES.busy, false);
  if (paths.length === 0) return withView(m, { kind: "validating", count: 0 });
  return withView(m, { kind: "validating", count: paths.length }, [{ type: "inspect", paths }]);
}

function onDragEnd(m: Model, cancelled: boolean): Step {
  if (!cancelled) return stay(m);
  const dragging = DRAGGING.has(m.view.kind) || (m.view.kind === "rejected" && !m.view.released);
  return dragging ? withView(m, { kind: "armed" }) : stay(m);
}

function onInspected(m: Model, result: DropInspection): Step {
  if (m.view.kind !== "validating") return stay(m); // stale: the drag already moved on
  if (result.ok) {
    return withView(m, { kind: "ready", count: result.file_count, totalSize: result.total_size });
  }
  const r = rejectionFor(result.items);
  return reject(m, r.reason, r.message, false);
}

function onRelease(m: Model, paths: string[]): Step {
  if (!preSend(m.view)) return stay(m);
  if (m.view.kind === "rejected") {
    // The user dropped something we already told them we cannot send: say so, send nothing.
    return reject(m, m.view.reason, m.view.message, true);
  }
  if (m.peer === null) return reject(m, "no_peer", MESSAGES.noPeer, true);
  if (m.busy) return reject(m, "busy", MESSAGES.busy, true);
  if (paths.length === 0) return reject(m, "invalid", MESSAGES.invalid, true);
  return withView(
    m,
    { kind: "sending", transferId: null, progress: 0, peer: m.peer, count: paths.length },
    [stage, { type: "send", paths }],
  );
}

function onSendFailed(m: Model, input: { code: string; message: string; items?: DropItem[] }): Step {
  if (m.view.kind !== "sending") return stay(m);
  const r = rejectionForError(input.code, input.items, m.view.peer);
  if (r) return reject(m, r.reason, r.message, true);
  return hold(m, (token) => ({ kind: "send_failed", message: MESSAGES.failed, token }), HOLD_MS.failed);
}

function onTransfer(m: Model, t: Transfer): Step {
  const done = isFinished(t.status);
  const next: Model = { ...m, busy: !done };
  const k = next.view.kind;
  const peer = t.peer_device_name ?? "device";
  const count = t.file_count;

  if (t.direction === "sent") {
    if (k === "sending") {
      const v = next.view as Extract<View, { kind: "sending" }>;
      if (v.transferId !== null && v.transferId !== t.transfer_id) return stay(next);
      if (!done) return withView(next, { ...v, transferId: t.transfer_id, progress: progressOf(t) });
      return t.status === "completed"
        ? hold(next, (token) => ({ kind: "send_success", peer, count, token }), HOLD_MS.success)
        : hold(
            next,
            (token) => ({
              kind: "send_failed",
              message: t.status === "partially_completed" ? MESSAGES.partial : MESSAGES.failed,
              token,
            }),
            HOLD_MS.failed,
          );
    }
    // A send we did not start from the edge (e.g. started elsewhere): show it if we are free.
    if (PRE_DRAG.has(k) && !done) {
      return withView(
        next,
        { kind: "sending", transferId: t.transfer_id, progress: progressOf(t), peer, count },
        [stage],
      );
    }
    return stay(next);
  }

  // received
  if (k === "receiving") {
    const v = next.view as Extract<View, { kind: "receiving" }>;
    if (v.transferId !== t.transfer_id) return stay(next);
    if (!done) return withView(next, { ...v, progress: progressOf(t) });
    return finishReceive(next, t, peer, count);
  }
  if (PRE_DRAG.has(k)) {
    if (!done) {
      return withView(
        next,
        { kind: "receiving", transferId: t.transfer_id, progress: progressOf(t), peer, count },
        [stage],
      );
    }
    return finishReceive(next, t, peer, count, true);
  }
  return stay(next);
}

function finishReceive(m: Model, t: Transfer, peer: string, count: number, needStage = false): Step {
  const effects: Effect[] = needStage ? [stage] : [];
  if (t.status === "completed") {
    return hold(m, (token) => ({ kind: "receive_success", peer, count, token }), HOLD_MS.success, effects);
  }
  const message = t.status === "partially_completed" ? MESSAGES.partial : MESSAGES.failed;
  return hold(m, (token) => ({ kind: "receive_failed", message, token }), HOLD_MS.failed, effects);
}

function onConnection(m: Model, peer: string | null): Step {
  const next = { ...m, peer };
  if (peer === null && DRAGGING.has(m.view.kind)) return reject(next, "no_peer", MESSAGES.noPeer, false);
  if (peer !== null && m.view.kind === "rejected" && !m.view.released && m.view.reason === "no_peer") {
    return withView(next, { kind: "armed" });
  }
  return stay(next);
}

function onSnapshot(m: Model, peer: string | null, active: Transfer | null): Step {
  const base: Model = { ...m, peer, busy: active !== null && !isFinished(active.status) };
  if (active && !isFinished(active.status)) return onTransfer(base, active);
  return stay(base);
}

function onTimer(m: Model, token: number): Step {
  const v = m.view;
  if (!TERMINAL_VIEWS.has(v.kind)) return stay(m);
  if (!("token" in v) || v.token !== token) return stay(m); // a stale timer from an earlier view
  return withView(m, { kind: "idle" }, [{ type: "window", mode: "idle" }]);
}

/** Text shown on the strip for a view. */
export function labelFor(v: View, peerName: string | null): string {
  switch (v.kind) {
    case "idle":
    case "approach":
      return "";
    case "armed":
    case "validating":
      return MESSAGES.dropToSend;
    case "handle":
      return "HandOff";
    case "ready":
      return MESSAGES.ready;
    case "rejected":
      return v.message;
    case "sending":
      return sendingTo(v.peer);
    case "send_success":
    case "receive_success":
      return MESSAGES.complete;
    case "send_failed":
    case "receive_failed":
      return v.message;
    case "receiving":
      return `Receiving from ${v.peer}`;
    case "panel":
      return peerName ?? "";
  }
}

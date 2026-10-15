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
  | SendingView
  | { kind: "send_success"; peer: string; count: number; token: number }
  | { kind: "send_failed"; message: string; token: number }
  | { kind: "receiving"; transferId: string; progress: number; peer: string; count: number }
  | { kind: "receive_success"; peer: string; count: number; token: number }
  | { kind: "receive_failed"; message: string; token: number }
  | { kind: "panel" }
  // Hand feedback only (a palm closing / opening). Not a transfer state: never a success view.
  | { kind: "hand_grab"; token: number }
  | { kind: "hand_release"; token: number }
  // The grabbed files, shown until the core says the grab is over (ADR-060). No timer here.
  | { kind: "holding"; names: string[]; count: number };

/** How far one transfer of a drop has got; `status` is its final state once it has one. */
export interface Part {
  progress: number;
  status: string | null;
}

/**
 * A drop in flight. A drop goes to every connected device (ADR-063), so it is a *batch* of
 * transfers: `ids` is null until the core's reply names them, and the drop only counts as done
 * when every one of them has ended.
 */
export interface SendingView {
  kind: "sending";
  transferId: string | null; // the first transfer, for single-device callers
  progress: number;
  peer: string;
  count: number;
  ids: string[] | null;
  parts: Record<string, Part>;
  /** Devices left out of this drop (busy with another transfer). */
  skipped: string[];
}

export type ViewKind = View["kind"];

export interface Model {
  view: View;
  /** What the strip calls the destination: one device's name, "3 devices", or null for none. */
  peer: string | null;
  /** How many devices are connected and online. */
  peerCount: number;
  /** Ids of the transfers in flight, either direction (at most one per device). */
  active: string[];
  /** Every connected device already has a transfer, so a new drop would have nowhere to go. */
  busy: boolean;
  /** Counter that gives every terminal view its own timer token. */
  seq: number;
  /** What the hand is holding (reported by the core); null when nothing. */
  held: { names: string[]; count: number } | null;
}

export type Input =
  | InteractionEvent
  | { type: "proximity"; phase: Proximity }
  | { type: "inspected"; result: DropInspection }
  | { type: "inspect_failed"; message: string }
  | { type: "sent"; transferId: string; transferIds?: string[]; skipped?: string[] }
  | { type: "send_failed"; code: string; message: string; items?: DropItem[] }
  | { type: "transfer"; transfer: Transfer }
  | { type: "connection"; peer: string | null; count?: number }
  | { type: "snapshot"; peer: string | null; count?: number; active: Transfer | null; actives?: Transfer[] }
  | { type: "held"; names: string[]; count: number }
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

export const HOLD_MS = { rejected: 2200, success: 2600, failed: 3600, hand: 900 } as const;

export const initialModel = (): Model => ({
  view: { kind: "idle" }, peer: null, peerCount: 0, active: [], busy: false, seq: 0, held: null,
});

/** With N devices connected, a drop is blocked only once all N already have a transfer. */
const busyFor = (peerCount: number, active: readonly string[]): boolean => active.length >= Math.max(1, peerCount);

function trackTransfer(m: Model, t: Transfer): Model {
  const rest = m.active.filter((id) => id !== t.transfer_id);
  const active = isFinished(t.status) ? rest : [...rest, t.transfer_id];
  return { ...m, active, busy: busyFor(m.peerCount, active) };
}

/** Short hand-feedback views: a transfer, a drag or a new gesture may take over at any time. */
const HAND_VIEWS: ReadonlySet<ViewKind> = new Set(["hand_grab", "hand_release", "holding"]);
const PRE_DRAG: ReadonlySet<ViewKind> = new Set([
  "idle", "approach", "armed", "handle", "hand_grab", "hand_release", "holding",
]);
const DRAGGING: ReadonlySet<ViewKind> = new Set(["validating", "ready"]);
const TERMINAL_VIEWS: ReadonlySet<ViewKind> = new Set([
  "rejected",
  "send_success",
  "send_failed",
  "receive_success",
  "receive_failed",
  "hand_grab",
  "hand_release",
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
      return m.view.kind === "handle" || m.view.kind === "armed" ? openPanel(m) : stay(m);
    case "inspected":
      return onInspected(m, input.result);
    case "inspect_failed":
      return m.view.kind === "validating" ? reject(m, "invalid", input.message, false) : stay(m);
    case "sent":
      return onSent(m, input);
    case "send_failed":
      return onSendFailed(m, input);
    case "transfer":
      return onTransfer(m, input.transfer);
    case "connection":
      return onConnection(m, input.peer, input.count);
    case "snapshot":
      return onSnapshot(m, input);
    case "open_panel":
      return openPanel(m);
    case "close_panel":
      return m.view.kind === "panel" ? withView(m, { kind: "idle" }, [{ type: "window", mode: "idle" }]) : stay(m);
    case "timer":
      return onTimer(m, input.token);
    case "gesture_detected":
      return onGesture(m, input.source, input.gesture);
    case "held":
      return onHeld(m, input.names, input.count);
    // Pointer detail the view reads directly. A hand-held drag arrives as a real OS drag, so
    // nothing else is needed here.
    default:
      return stay(m);
  }
}

/** The COPY gesture's two beats (ADR-057). Feedback only: it never starts or reports a transfer. */
function onGesture(m: Model, source: string, gesture: string): Step {
  if (source !== "cv" || !PRE_DRAG.has(m.view.kind)) return stay(m);
  if (gesture === "palm_grab") {
    return hold(m, (token) => ({ kind: "hand_grab", token }), HOLD_MS.hand, [stage]);
  }
  if (gesture === "palm_release") {
    return hold(m, (token) => ({ kind: "hand_release", token }), HOLD_MS.hand, [stage]);
  }
  return stay(m);
}

/** What the hand is holding changed (ADR-060). The core decides when it starts and ends. */
function onHeld(m: Model, names: string[], count: number): Step {
  if (count <= 0) {
    const next: Model = { ...m, held: null };
    return m.view.kind === "holding"
      ? withView(next, { kind: "idle" }, [{ type: "window", mode: "idle" }])
      : stay(next);
  }
  const held = { names, count };
  const next: Model = { ...m, held };
  if (!PRE_DRAG.has(m.view.kind)) return stay(next); // shown once the current view is over
  return withView(next, { kind: "holding", names, count }, [stage]);
}

function openPanel(m: Model): Step {
  if (!PRE_DRAG.has(m.view.kind)) return stay(m);
  return withView(m, { kind: "panel" }, [{ type: "window", mode: "panel" }]);
}

function onProximity(m: Model, phase: Proximity): Step {
  const k = m.view.kind;
  if (phase === "far") {
    // The pointer left: anything that was only waiting for a drop is over.
    if (HAND_VIEWS.has(k)) return stay(m); // the hand animation ends on its own timer
    return preSend(m.view) && k !== "idle" ? withView(m, { kind: "idle" }) : stay(m);
  }
  if (!PRE_DRAG.has(k)) return stay(m);
  // Pressing the button on the handle looks like a drag to the pointer watcher, but it is the
  // click that opens the panel. Do not turn the handle into the drop strip underneath it.
  if (k === "handle" && phase === "drag") return stay(m);
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
    { kind: "sending", transferId: null, progress: 0, peer: m.peer, count: paths.length, ids: null, parts: {}, skipped: [] },
    [stage, { type: "send", paths }],
  );
}

/** The core named the transfers of this drop. Results that arrived earlier now count. */
function onSent(m: Model, input: { transferId: string; transferIds?: string[]; skipped?: string[] }): Step {
  const v = m.view;
  if (v.kind !== "sending" || v.ids !== null) return stay(m);
  const ids = input.transferIds && input.transferIds.length > 0 ? input.transferIds : [input.transferId];
  const parts: Record<string, Part> = {};
  for (const id of ids) {
    const seen = v.parts[id];
    if (seen) parts[id] = seen;
  }
  return settle(m, { ...v, transferId: input.transferId, ids, parts, skipped: input.skipped ?? [] });
}

function averageProgress(v: SendingView): number {
  const ids = v.ids ?? Object.keys(v.parts);
  if (ids.length === 0) return 0;
  const sum = ids.reduce((acc, id) => acc + (v.parts[id]?.progress ?? 0), 0);
  return Math.min(0.99, sum / ids.length);
}

/**
 * Honesty rule for a batch: success only when EVERY transfer the core created is completed.
 * Until the reply names the batch (`ids` is null) nothing can end the drop.
 */
function settle(m: Model, v: SendingView): Step {
  const shown: SendingView = { ...v, progress: averageProgress(v) };
  if (v.ids === null) return withView(m, shown);
  const results = v.ids.map((id) => v.parts[id]?.status ?? null);
  if (results.some((r) => r === null)) return withView(m, shown);

  const total = results.length;
  const ok = results.filter((r) => r === "completed").length;
  const effects: Effect[] =
    v.skipped.length > 0 ? [{ type: "notify", title: "HandOff", body: MESSAGES.skippedBusy(v.skipped) }] : [];
  if (ok === total) {
    return hold(m, (token) => ({ kind: "send_success", peer: v.peer, count: v.count, token }), HOLD_MS.success, effects);
  }
  const message =
    total === 1
      ? results[0] === "partially_completed" ? MESSAGES.partial : MESSAGES.failed
      : ok === 0 ? MESSAGES.failed : MESSAGES.partialSend(ok, total);
  return hold(m, (token) => ({ kind: "send_failed", message, token }), HOLD_MS.failed, effects);
}

function onSentUpdate(m: Model, t: Transfer): Step {
  const v = m.view as SendingView;
  if (v.ids !== null && !v.ids.includes(t.transfer_id)) return stay(m); // another drop's transfer
  const done = isFinished(t.status);
  const part: Part = { progress: done ? 1 : progressOf(t), status: done ? t.status : null };
  return settle(m, {
    ...v,
    transferId: v.transferId ?? t.transfer_id,
    parts: { ...v.parts, [t.transfer_id]: part },
  });
}

function onSendFailed(m: Model, input: { code: string; message: string; items?: DropItem[] }): Step {
  if (m.view.kind !== "sending") return stay(m);
  const r = rejectionForError(input.code, input.items, m.view.peer);
  if (r) return reject(m, r.reason, r.message, true);
  return hold(m, (token) => ({ kind: "send_failed", message: MESSAGES.failed, token }), HOLD_MS.failed);
}

function onTransfer(m: Model, t: Transfer): Step {
  const done = isFinished(t.status);
  const next = trackTransfer(m, t);
  const k = next.view.kind;
  const peer = t.peer_device_name ?? "device";
  const count = t.file_count;

  if (t.direction === "sent") {
    if (k === "sending") return onSentUpdate(next, t);
    // A send we did not start from the edge (e.g. started elsewhere): show it if we are free.
    if (PRE_DRAG.has(k) && !done) {
      const progress = progressOf(t);
      return withView(
        next,
        {
          kind: "sending", transferId: t.transfer_id, progress, peer, count, ids: [t.transfer_id],
          parts: { [t.transfer_id]: { progress, status: null } }, skipped: [],
        },
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

function onConnection(m: Model, peer: string | null, count?: number): Step {
  const peerCount = count ?? (peer === null ? 0 : 1);
  const next = {
    ...m, peer, peerCount, busy: m.active.length > 0 ? busyFor(peerCount, m.active) : m.busy,
  };
  if (peer === null && DRAGGING.has(m.view.kind)) return reject(next, "no_peer", MESSAGES.noPeer, false);
  if (peer !== null && m.view.kind === "rejected" && !m.view.released && m.view.reason === "no_peer") {
    return withView(next, { kind: "armed" });
  }
  return stay(next);
}

function onSnapshot(
  m: Model,
  input: { peer: string | null; count?: number; active: Transfer | null; actives?: Transfer[] },
): Step {
  const running = (input.actives ?? (input.active ? [input.active] : [])).filter((t) => !isFinished(t.status));
  const peerCount = input.count ?? (input.peer === null ? 0 : 1);
  const active = running.map((t) => t.transfer_id);
  let model: Model = { ...m, peer: input.peer, peerCount, active, busy: busyFor(peerCount, active) && active.length > 0 };
  const effects: Effect[] = [];
  for (const t of running) {
    const s = onTransfer(model, t);
    model = s.model;
    effects.push(...s.effects);
  }
  return step(model, effects);
}

function onTimer(m: Model, token: number): Step {
  const v = m.view;
  if (!TERMINAL_VIEWS.has(v.kind)) return stay(m);
  if (!("token" in v) || v.token !== token) return stay(m); // a stale timer from an earlier view
  // Something is still held: go back to showing it, not to nothing.
  if (m.held) return withView(m, { kind: "holding", ...m.held }, [stage]);
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
    case "hand_grab":
      return MESSAGES.grabbed;
    case "hand_release":
      return MESSAGES.released;
    case "holding":
      return MESSAGES.holding(v.count);
  }
}

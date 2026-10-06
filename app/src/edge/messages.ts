// User-facing wording for the edge. Pure, so every rejection path is unit-tested.
import type { DropItem } from "../types";

export type RejectReason =
  | "no_peer"
  | "busy"
  | "offline"
  | "unsupported"
  | "folder"
  | "shortcut"
  | "too_large"
  | "missing"
  | "invalid";

export interface Rejection {
  reason: RejectReason;
  message: string;
}

export const MESSAGES = {
  dropToSend: "Drop to send",
  ready: "Ready to send",
  noPeer: "No HandOff device connected",
  busy: "A transfer is already in progress",
  unsupported: "This file type is not supported",
  folder: "Folders are not supported",
  shortcut: "Shortcuts are not supported",
  tooLarge: "Files over 50 MB are not supported",
  missing: "That file no longer exists",
  invalid: "This item can't be sent",
  complete: "Transfer complete",
  failed: "Transfer failed",
  partial: "Some files failed",
} as const;

export const sendingTo = (device: string): string => `Sending to ${device}`;
export const receivingFrom = (device: string): string => `Receiving from ${device}`;
export const offlineMessage = (device: string): string => `${device} is offline`;

const BY_REASON: Record<string, Rejection> = {
  directory: { reason: "folder", message: MESSAGES.folder },
  symlink: { reason: "shortcut", message: MESSAGES.shortcut },
  unsupported_type: { reason: "unsupported", message: MESSAGES.unsupported },
  executable_content: { reason: "unsupported", message: MESSAGES.unsupported },
  too_large: { reason: "too_large", message: MESSAGES.tooLarge },
  missing: { reason: "missing", message: MESSAGES.missing },
};

/** Why a drop was refused, from the core's per-item verdicts (first bad item wins). */
export function rejectionFor(items: readonly DropItem[]): Rejection {
  const bad = items.find((i) => !i.ok);
  if (!bad) return { reason: "invalid", message: MESSAGES.invalid };
  return (bad.reason !== undefined && BY_REASON[bad.reason]) || { reason: "invalid", message: MESSAGES.invalid };
}

/** Maps a failed `drop.send` to what the user should see, or null if it is a plain failure. */
export function rejectionForError(code: string, items: readonly DropItem[] | undefined, peer: string | null): Rejection | null {
  if (items && items.some((i) => !i.ok)) return rejectionFor(items);
  switch (code) {
    case "DEVICE_NOT_FOUND":
      return { reason: "no_peer", message: MESSAGES.noPeer };
    case "DEVICE_OFFLINE":
      return { reason: "offline", message: peer ? offlineMessage(peer) : MESSAGES.noPeer };
    case "INVALID_STATE":
      return { reason: "busy", message: MESSAGES.busy };
    default:
      return null;
  }
}

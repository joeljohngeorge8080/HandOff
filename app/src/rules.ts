// UI rules derived from the requirements (kept pure so they are unit-tested).
import type { Connection, Transfer } from "./types";
import { FINISHED_STATES } from "./types";

export interface SendContext {
  selectedCount: number;
  connection: Connection;
  activeTransfer: Transfer | null;
}

/** FR: Send is enabled only with >=1 selected file AND a connected peer; one transfer at a time. */
export function canSend(ctx: SendContext): boolean {
  return (
    ctx.selectedCount >= 1 &&
    ctx.connection.connected &&
    ctx.connection.device !== null &&
    ctx.activeTransfer === null
  );
}

/** Why Send is disabled, for a tooltip/hint. Empty string when enabled. */
export function sendBlockedReason(ctx: SendContext): string {
  if (ctx.activeTransfer !== null) return "A transfer is already in progress.";
  if (!ctx.connection.connected || ctx.connection.device === null) return "Connect to a device first.";
  if (ctx.selectedCount < 1) return "Select at least one file.";
  return "";
}

/** ADR-047: switching peers is rejected while a transfer is active. */
export function canSwitchPeer(activeTransfer: Transfer | null): boolean {
  return activeTransfer === null;
}

export function isFinished(status: string): boolean {
  return (FINISHED_STATES as readonly string[]).includes(status);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

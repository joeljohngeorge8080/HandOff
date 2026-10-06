// UI rules derived from the requirements (kept pure so they are unit-tested).
import type { Transfer } from "./types";
import { FINISHED_STATES } from "./types";

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

/** Shortens a long folder path in the middle, keeping the start and the folder name visible. */
export function shortenPath(path: string, max = 44): string {
  if (path.length <= max) return path;
  const keepEnd = Math.floor((max - 1) * 0.6);
  const keepStart = max - 1 - keepEnd;
  return `${path.slice(0, keepStart)}…${path.slice(path.length - keepEnd)}`;
}

/** The connected peer's name, or null unless it is connected and online. */
export function connectedPeerName(connection: { connected: boolean; device: { device_name: string } | null }): string | null {
  return connection.connected && connection.device ? connection.device.device_name : null;
}

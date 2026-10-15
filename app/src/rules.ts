// UI rules derived from the requirements (kept pure so they are unit-tested).
import type { Peer } from "./types";
import { FINISHED_STATES } from "./types";

/** ADR-063: how many devices can be connected at the same time (mirrors the core). */
export const MAX_CONNECTED_PEERS = 4;

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

type ConnectionLike = {
  connected: boolean;
  device: { device_name: string } | null;
  devices?: Pick<Peer, "device_name" | "status">[];
};

/** The devices a drop can go to right now (connected and online). */
function onlineDevices(connection: ConnectionLike): { device_name: string }[] {
  if (!connection.connected) return [];
  if (connection.devices === undefined) return connection.device ? [connection.device] : [];
  return connection.devices.filter((d) => d.status === "connected");
}

export function connectedPeerCount(connection: ConnectionLike): number {
  return onlineDevices(connection).length;
}

/** What the strip calls the destination: one device's name, "3 devices", or null for none. */
export function connectedPeerName(connection: ConnectionLike): string | null {
  const online = onlineDevices(connection);
  if (online.length === 0) return null;
  return online.length === 1 ? (online[0]?.device_name ?? null) : `${online.length} devices`;
}

/** True when every slot holds an online device, so another one cannot be connected. */
export function peerLimitReached(connection: ConnectionLike): boolean {
  return connectedPeerCount(connection) >= MAX_CONNECTED_PEERS;
}

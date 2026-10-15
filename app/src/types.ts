// Shapes returned by the core over IPC (backend/src/handoff/ipc/dispatcher.py).

export interface Peer {
  device_id: string;
  device_name: string;
  address: string | null;
  port: number | null;
  status: string; // available | connected | offline
  is_trusted?: boolean;
}

export interface Connection {
  connected: boolean;
  /** The first connected device (kept for older callers); `devices` is the full list. */
  device: Peer | null;
  /** Every device holding a connection slot, online or offline (ADR-063). */
  devices?: Peer[];
}

export interface TransferFile {
  name: string;
  size: number;
  status: string;
  failure_code: string | null;
}

export interface Transfer {
  transfer_id: string;
  direction: "sent" | "received";
  peer_device_id: string | null;
  peer_device_name: string | null;
  file_count: number;
  total_size: number;
  archive_size: number | null;
  bytes_transferred: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  files: TransferFile[];
}

export interface HandControl {
  state: "off" | "starting" | "tracking" | "no_hand" | "error";
  message: string;
  code?: string;
}

export interface Snapshot {
  device: { device_id: string; device_name: string };
  receive_directory: string;
  connection: Connection;
  hand_control?: HandControl;
  active_transfer: Transfer | null;
  /** Every transfer in progress; one per connected device at most (ADR-063). */
  active_transfers?: Transfer[];
  recent_history: Transfer[];
}

export interface CoreError {
  code: string;
  message: string;
  details?: unknown;
}

/** One dropped item as judged by `drop.inspect` / a rejected `drop.send`. */
export interface DropItem {
  name: string;
  ok: boolean;
  size?: number;
  code?: string;
  reason?: string;
  message?: string;
}

/** A device a drop was not sent to, and why (e.g. it is busy with another transfer). */
export interface SkippedPeer {
  device_id: string;
  device_name: string;
  reason: string;
}

/** The reply to `drop.send`: one transfer per device that took it. */
export interface DropResult {
  transfer: Transfer;
  transfers?: Transfer[];
  skipped?: SkippedPeer[];
}

export interface DropInspection {
  ok: boolean;
  file_count: number;
  total_size: number;
  items: DropItem[];
}

export const FINISHED_STATES = ["completed", "failed", "partially_completed"] as const;

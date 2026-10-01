// Shapes returned by the core over IPC (backend/src/handoff/ipc/dispatcher.py).

export interface FileItem {
  id: string;
  name: string;
  size: number;
  extension: string;
  source: string;
  created_at: string;
}

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
  device: Peer | null;
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

export interface Snapshot {
  device: { device_id: string; device_name: string };
  receive_mode: boolean;
  connection: Connection;
  active_transfer: Transfer | null;
  recent_history: Transfer[];
}

export interface CoreError {
  code: string;
  message: string;
  details?: unknown;
}

export const FINISHED_STATES = ["completed", "failed", "partially_completed"] as const;

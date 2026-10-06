// Decides which desktop notifications to show (FR-033) by diffing transfer statuses between
// updates. Pure; the Tauri notification call lives in notifier.ts.
import type { Transfer } from "./types";
import { isFinished } from "./rules";

export interface Notice {
  title: string;
  body: string;
}

export function noticeFor(t: Transfer): Notice | null {
  const peer = t.peer_device_name ?? "another device";
  const n = t.file_count;
  const files = `${n} file${n === 1 ? "" : "s"}`;
  switch (t.status) {
    case "completed":
      return t.direction === "received"
        ? { title: "HandOff", body: `${files} received from ${peer}.` }
        : { title: "HandOff", body: `Transfer complete: ${files} sent to ${peer}.` };
    case "partially_completed":
      return { title: "HandOff", body: `Some files failed to transfer with ${peer}.` };
    case "failed":
      return { title: "HandOff", body: `Transfer failed (${peer}).` };
    default:
      return null;
  }
}

/**
 * Tracks the last-seen status of each transfer and reports each transfer's final state once.
 * The first poll only records history that already existed, so a restart never replays old
 * notifications.
 */
export class TransferWatcher {
  private seen = new Map<string, string>();
  private primed = false;

  update(transfers: readonly Transfer[]): Notice[] {
    const notices: Notice[] = [];
    for (const t of transfers) {
      const before = this.seen.get(t.transfer_id);
      if (this.primed && before !== t.status && isFinished(t.status)) {
        const notice = noticeFor(t);
        if (notice) notices.push(notice);
      }
      this.seen.set(t.transfer_id, t.status);
    }
    this.primed = true;
    return notices;
  }
}

import { describe, expect, it } from "vitest";
import { noticeFor, TransferWatcher } from "./notifications";
import type { Transfer } from "./types";

function t(over: Partial<Transfer>): Transfer {
  return {
    transfer_id: "t1", direction: "sent", peer_device_id: "d", peer_device_name: "Aaron-Laptop",
    file_count: 3, total_size: 1, archive_size: null, bytes_transferred: 0, status: "created",
    error_code: null, error_message: null, created_at: "", completed_at: null, files: [], ...over,
  };
}

describe("noticeFor", () => {
  it("reports a received transfer with count and sender", () => {
    expect(noticeFor(t({ direction: "received", status: "completed" }))?.body).toBe(
      "3 files received from Aaron-Laptop.",
    );
  });
  it("reports a completed send", () => {
    expect(noticeFor(t({ status: "completed" }))?.body).toMatch(/completed successfully/);
  });
  it("reports failure and partial completion", () => {
    expect(noticeFor(t({ status: "failed" }))?.body).toMatch(/failed/);
    expect(noticeFor(t({ status: "partially_completed" }))?.body).toMatch(/some files failed/);
  });
  it("is silent for in-flight states", () => {
    for (const s of ["created", "validating", "accepted", "transferring"]) {
      expect(noticeFor(t({ status: s }))).toBeNull();
    }
  });
  it("uses singular wording for one file", () => {
    expect(noticeFor(t({ direction: "received", status: "completed", file_count: 1 }))?.body).toMatch(/^1 file received/);
  });
});

describe("TransferWatcher", () => {
  it("does not replay history that existed at startup", () => {
    const w = new TransferWatcher();
    expect(w.update([t({ status: "completed" })])).toEqual([]);
  });
  it("notifies once when a transfer reaches a final state", () => {
    const w = new TransferWatcher();
    w.update([]);
    expect(w.update([t({ status: "transferring" })])).toEqual([]);
    expect(w.update([t({ status: "completed" })])).toHaveLength(1);
    expect(w.update([t({ status: "completed" })])).toEqual([]);
  });
  it("notifies for a received transfer first seen already finished", () => {
    const w = new TransferWatcher();
    w.update([]);
    expect(w.update([t({ transfer_id: "r1", direction: "received", status: "completed" })])).toHaveLength(1);
  });
  it("notifies for failures", () => {
    const w = new TransferWatcher();
    w.update([t({ status: "transferring" })]);
    expect(w.update([t({ status: "failed" })])[0]?.body).toMatch(/failed/);
  });
});

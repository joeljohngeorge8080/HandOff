import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { open } from "@tauri-apps/plugin-dialog";
import { core, CoreRequestError } from "./ipc";
import { el } from "./dom";
import { canSend, sendBlockedReason } from "./rules";
import { TransferWatcher } from "./notifications";
import { Gallery } from "./views/gallery";
import { DevicePanel } from "./views/devices";
import { renderActive, renderHistory } from "./views/history";
import type { FileItem, Peer, Snapshot, Transfer } from "./types";

const ALLOWED = ["txt", "jpg", "mp4", "exe"]; // dialog filter; the core re-validates (case-insensitive)
const POLL_MS = 1500;

let snapshot: Snapshot | null = null;
let selectedIds: string[] = [];
let confirmingDelete = false;
const watcher = new TransferWatcher();

const gallery = new Gallery((ids) => {
  selectedIds = ids;
  confirmingDelete = false;
  renderToolbar();
});
const devices = new DevicePanel(
  async (id) => {
    await core("devices.connect", { device_id: id });
    await refresh();
  },
  () => void refreshDevices(),
);

const app = document.getElementById("app") as HTMLElement;
const header = el("header", { class: "topbar" });
const toolbar = el("div", { class: "toolbar" });
const banner = el("div", { class: "banner" });
const progress = el("div");
const historyHost = el("div");
const retentionHost = el("div", { class: "retention" });
app.append(
  header,
  el("main", { class: "layout" },
    el("section", { class: "panel files" }, el("h2", { text: "Files" }), toolbar, banner, gallery.root),
    el("aside", { class: "side" }, devices.root, progress, historyHost, retentionHost)),
);

function say(text: string, kind: "" | "error" | "ok" = ""): void {
  banner.textContent = text;
  banner.className = `banner ${kind}`;
}

function errText(e: unknown): string {
  return e instanceof CoreRequestError ? e.message : String(e);
}

function renderHeader(): void {
  const receive = el("label", { class: "toggle", title: "Accept incoming transfers from the connected device" });
  const box = el("input", { type: "checkbox" }) as HTMLInputElement;
  box.checked = snapshot?.receive_mode ?? false;
  box.addEventListener("change", async () => {
    try {
      await core("receive_mode.set", { enabled: box.checked });
    } catch (e) {
      say(errText(e), "error");
    }
    await refresh();
  });
  receive.append(box, el("span", { text: box.checked ? "Receive: ON" : "Receive: OFF" }));
  header.replaceChildren(
    el("h1", { text: "HandOff" }),
    el("span", { class: "muted", text: snapshot ? `This device: ${snapshot.device.device_name}` : "Starting…" }),
    el("span", { class: "grow" }),
    receive,
  );
}

function renderToolbar(): void {
  const ctx = {
    selectedCount: selectedIds.length,
    connection: snapshot?.connection ?? { connected: false, device: null },
    activeTransfer: snapshot?.active_transfer ?? null,
  };
  const dest = ctx.connection.device;
  const add = el("button", { text: "Add files" });
  add.addEventListener("click", () => void addFiles());

  const del = el("button", {
    class: confirmingDelete ? "danger" : "",
    text: confirmingDelete ? `Delete ${selectedIds.length} from HandOff?` : "Delete",
    disabled: selectedIds.length === 0,
    title: "Removes HandOff's copy only. Your original file is not touched.",
  });
  del.addEventListener("click", () => {
    if (!confirmingDelete) {
      confirmingDelete = true;
      renderToolbar();
    } else void deleteSelected();
  });

  const send = el("button", {
    class: "primary",
    text: dest && ctx.connection.connected ? `Send to ${dest.device_name}` : "Send",
    disabled: !canSend(ctx),
    title: sendBlockedReason(ctx),
  });
  send.addEventListener("click", () => void sendSelected());
  toolbar.replaceChildren(add, del, el("span", { class: "grow" }), el("span", { class: "muted", text: `${selectedIds.length} selected` }), send);
}

async function addFiles(): Promise<void> {
  const picked = await open({ multiple: true, filters: [{ name: "HandOff files", extensions: ALLOWED }] });
  const paths = Array.isArray(picked) ? picked : picked ? [picked] : [];
  if (paths.length === 0) return;
  try {
    const res = await core<{ added: FileItem[]; rejected: { path: string; error: { message: string } }[] }>(
      "files.add", { paths });
    const bad = res.rejected.map((r) => `${r.path.split(/[\\/]/).pop()}: ${r.error.message}`);
    if (bad.length) say(`${res.added.length} added. Rejected — ${bad.join(" · ")}`, "error");
    else say(`${res.added.length} file${res.added.length === 1 ? "" : "s"} added.`, "ok");
  } catch (e) {
    say(errText(e), "error");
  }
  await refresh();
}

async function deleteSelected(): Promise<void> {
  const ids = [...selectedIds];
  confirmingDelete = false;
  for (const id of ids) {
    try {
      await core("files.delete", { file_id: id });
    } catch (e) {
      say(errText(e), "error");
    }
  }
  await refresh();
}

async function sendSelected(): Promise<void> {
  const dest = snapshot?.connection.device;
  if (!dest) return;
  try {
    await core("transfer.create", { file_ids: selectedIds, destination_device_id: dest.device_id });
    say(`Sending ${selectedIds.length} file${selectedIds.length === 1 ? "" : "s"} to ${dest.device_name}…`, "ok");
  } catch (e) {
    say(errText(e), "error");
  }
  await refresh();
}

async function notify(title: string, body: string): Promise<void> {
  try {
    let ok = await isPermissionGranted();
    if (!ok) ok = (await requestPermission()) === "granted";
    if (ok) sendNotification({ title, body });
  } catch {
    // Notifications are best-effort; the in-app history still shows the result.
  }
}

async function refreshDevices(): Promise<void> {
  try {
    const { devices: found } = await core<{ devices: Peer[] }>("devices.discover");
    devices.update(found, snapshot?.connection ?? { connected: false, device: null }, snapshot?.active_transfer ?? null);
  } catch (e) {
    say(errText(e), "error");
  }
}

function renderRetention(): void {
  const select = el("select") as HTMLSelectElement;
  for (const [days, text] of [[0, "Keep forever"], [7, "7 days"], [30, "30 days"], [90, "90 days"]] as const) {
    select.append(new Option(text, String(days)));
  }
  void core<{ settings: Record<string, unknown> }>("settings.get").then((s) => {
    select.value = String(s.settings["history_retention"] ?? 0);
  }).catch(() => undefined);
  select.addEventListener("change", () => {
    void core("settings.set", { key: "history_retention", value: Number(select.value) })
      .catch((e) => say(errText(e), "error"));
  });
  retentionHost.replaceChildren(el("label", {}, el("span", { class: "muted", text: "History retention " }), select));
}

let refreshing = false;

async function refresh(): Promise<void> {
  if (refreshing) return; // the poll timer and user actions must not overlap
  refreshing = true;
  try {
    snapshot = await core<Snapshot>("status.snapshot");
    const { files } = await core<{ files: FileItem[] }>("files.list");
    gallery.setFiles(files);
    const seen: Transfer[] = snapshot.active_transfer
      ? [snapshot.active_transfer, ...snapshot.recent_history] : snapshot.recent_history;
    for (const n of watcher.update(seen)) void notify(n.title, n.body);
    renderHeader();
    renderToolbar();
    progress.replaceChildren(renderActive(snapshot.active_transfer));
    historyHost.replaceChildren(renderHistory(snapshot.recent_history));
    if (devices.isOpen()) await refreshDevices();
    else devices.update([], snapshot.connection, snapshot.active_transfer);
  } catch (e) {
    say(errText(e), "error");
  } finally {
    refreshing = false;
  }
}

renderHeader();
renderToolbar();
renderRetention();
void refresh();
setInterval(() => void refresh(), POLL_MS);

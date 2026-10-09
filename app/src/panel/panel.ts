// The compact control panel that opens when the edge handle is clicked: who you are connected
// to, nearby devices, where received files go, recent transfers, history retention, quit.
//
// It talks to the core only through the IPC bridge. The receive folder is chosen with the
// native folder picker and validated by the core (the receiving machine decides its own
// destination; nothing here, and nothing from the network, supplies a path to write to).
import { el } from "../dom";
import { handControlLabel } from "../input/cv";
import { canSwitchPeer, formatBytes, shortenPath } from "../rules";
import type { HandControl, Peer, Snapshot, Transfer } from "../types";

export interface PanelDeps {
  core: <T>(action: string, payload?: Record<string, unknown>) => Promise<T>;
  pickFolder: (defaultPath: string) => Promise<string | null>;
  quit: () => void;
  onClose: () => void;
}

const RETENTION: [string, number][] = [["Forever", 0], ["7 days", 7], ["30 days", 30], ["90 days", 90]];
const REFRESH_MS = 3000;

function describe(t: Transfer): string {
  const peer = t.peer_device_name ?? "unknown device";
  const dir = t.direction === "sent" ? `To ${peer}` : `From ${peer}`;
  return `${dir} · ${t.file_count} file${t.file_count === 1 ? "" : "s"} · ${formatBytes(t.total_size)}`;
}

export class Panel {
  readonly root: HTMLElement;
  private snapshot: Snapshot | null = null;
  private peers: Peer[] = [];
  private retention = 90;
  private handEnabled = false;
  private hand: HandControl | null = null;
  private error = "";
  private busyId: string | null = null;
  private dialogOpen = false;
  private open_ = false;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly deps: PanelDeps) {
    this.root = document.getElementById("panel") as HTMLElement;
    window.addEventListener("blur", () => {
      // The folder picker is a separate window and takes focus: that is not "clicked away".
      if (this.open_ && !this.dialogOpen) this.close();
    });
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.open_) this.close();
    });
  }

  get isOpen(): boolean {
    return this.open_;
  }

  show(): void {
    this.open_ = true;
    this.error = "";
    document.body.dataset.panel = "1";
    this.render();
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), REFRESH_MS);
  }

  /** Live status pushed by the core (`cv.status`). */
  setHandControl(status: HandControl): void {
    this.hand = status;
    if (this.open_ && !this.dialogOpen && this.busyId === null) this.render();
  }

  hide(): void {
    this.open_ = false;
    delete document.body.dataset.panel;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private close(): void {
    this.hide();
    this.deps.onClose();
  }

  private async refresh(): Promise<void> {
    try {
      const [snap, found, settings] = await Promise.all([
        this.deps.core<Snapshot>("status.snapshot"),
        this.deps.core<{ devices: Peer[] }>("devices.discover"),
        this.deps.core<{ settings: Record<string, unknown> }>("settings.get"),
      ]);
      this.snapshot = snap;
      this.peers = found.devices;
      const r = settings.settings.history_retention;
      if (typeof r === "number") this.retention = r;
      this.handEnabled = settings.settings.hand_control_enabled === true;
      this.hand = snap.hand_control ?? this.hand;
    } catch (e) {
      this.error = (e as { message?: string })?.message ?? "Could not reach HandOff";
    }
    if (this.open_ && !this.dialogOpen && this.busyId === null) this.render();
  }

  private render(): void {
    const snap = this.snapshot;
    const conn = snap?.connection;
    const active = snap?.active_transfer ?? null;
    const head = el("div", { class: "row" }, el("h1", { text: snap?.device.device_name ?? "HandOff" }));
    const closeBtn = el("button", { class: "close", text: "×", title: "Close" });
    closeBtn.addEventListener("click", () => this.close());
    head.append(closeBtn);

    const peerRow = el("div", { class: "card row" },
      el("span", { class: `dot ${conn?.device?.status === "connected" ? "on" : conn?.device ? "offline" : ""}` }),
      el("div", { class: "grow" },
        el("div", { text: conn?.device ? conn.device.device_name : "Not connected" }),
        el("div", { class: "muted", text: conn?.connected ? "Dropped files are sent here" : "Choose a device below" })));

    this.root.replaceChildren(head, peerRow, this.devicesSection(active), this.folderSection(snap), this.handSection(), this.historySection(snap));
    if (this.error) this.root.append(el("p", { class: "error", text: this.error }));
    const quit = el("button", { class: "quit", text: "Quit HandOff" });
    quit.addEventListener("click", () => this.deps.quit());
    this.root.append(quit);
  }

  private devicesSection(active: Transfer | null): HTMLElement {
    const box = el("section", {}, el("h2", { text: "Nearby devices" }));
    const list = el("ul", { class: "list" });
    const current = this.snapshot?.connection.device?.device_id;
    if (this.peers.length === 0) list.append(el("li", { class: "muted", text: "No devices found on this network." }));
    const mayConnect = canSwitchPeer(active);
    for (const p of this.peers) {
      const isCurrent = current === p.device_id && p.status === "connected";
      const btn = el("button", {
        text: isCurrent ? "Connected" : this.busyId === p.device_id ? "Connecting…" : "Connect",
        disabled: isCurrent || !mayConnect || this.busyId !== null,
        title: mayConnect ? "" : "Finish the current transfer before switching devices.",
      });
      btn.addEventListener("click", () => void this.connect(p.device_id));
      list.append(el("li", {},
        el("span", { class: `dot ${p.status === "connected" ? "on" : p.status === "offline" ? "offline" : ""}` }),
        el("span", { class: "grow ellipsis", text: p.device_name }),
        btn));
    }
    box.append(list);
    return box;
  }

  private folderSection(snap: Snapshot | null): HTMLElement {
    const dir = snap?.receive_directory ?? "";
    const change = el("button", { text: "Change…" });
    change.addEventListener("click", () => void this.pickFolder(dir));
    return el("section", {},
      el("h2", { text: "Received files go to" }),
      el("div", { class: "card row" },
        el("div", { class: "grow ellipsis", title: dir, text: shortenPath(dir) }),
        change));
  }

  private handSection(): HTMLElement {
    const box = el("input", { type: "checkbox" });
    box.checked = this.handEnabled;
    box.addEventListener("change", () => void this.toggleHandControl(box.checked));
    const bad = this.handEnabled && this.hand?.state === "error";
    return el("section", {},
      el("h2", { text: "Hand control" }),
      el("label", { class: "card row", title: "Uses the camera to move the pointer and drag files" },
        el("span", { class: "grow", text: "Control with my hand (camera)" }), box),
      el("p", { class: bad ? "error" : "muted", text: handControlLabel(this.handEnabled, this.hand) }));
  }

  private async toggleHandControl(on: boolean): Promise<void> {
    this.handEnabled = on;
    this.hand = on ? { state: "starting", message: "" } : null;
    this.render();
    try {
      await this.deps.core("settings.set", { key: "hand_control_enabled", value: on });
      this.error = "";
    } catch (e) {
      this.handEnabled = !on;
      this.error = (e as { message?: string })?.message ?? "Could not change hand control";
    }
    await this.refresh();
    this.render();
  }

  private historySection(snap: Snapshot | null): HTMLElement {
    const box = el("section", {}, el("h2", { text: "Recent" }));
    const items = (snap?.recent_history ?? []).slice(0, 5);
    if (items.length === 0) box.append(el("p", { class: "muted", text: "No transfers yet." }));
    const list = el("ul", { class: "list" });
    for (const t of items) {
      list.append(el("li", {},
        el("span", { class: `badge ${t.status}`, text: t.status.replace("_", " ") }),
        el("span", { class: "grow ellipsis", title: describe(t), text: describe(t) })));
    }
    box.append(list);
    const select = el("select");
    for (const [label, days] of RETENTION) {
      const o = el("option", { text: label });
      o.value = String(days);
      o.selected = days === this.retention;
      select.append(o);
    }
    select.addEventListener("change", () => void this.setRetention(Number(select.value)));
    box.append(el("div", { class: "row", title: "How long transfer history is kept" },
      el("span", { class: "muted grow", text: "Keep history" }), select));
    return box;
  }

  private async connect(deviceId: string): Promise<void> {
    this.busyId = deviceId;
    this.error = "";
    this.render();
    try {
      await this.deps.core("devices.connect", { device_id: deviceId });
    } catch (e) {
      this.error = (e as { message?: string })?.message ?? "Could not connect";
    } finally {
      this.busyId = null;
      await this.refresh();
      this.render();
    }
  }

  private async pickFolder(current: string): Promise<void> {
    this.dialogOpen = true;
    try {
      const chosen = await this.deps.pickFolder(current);
      if (!chosen) return; // cancelled: keep the existing folder
      await this.deps.core("settings.set", { key: "receive_directory", value: chosen });
      this.error = "";
    } catch (e) {
      this.error = (e as { message?: string })?.message ?? "That folder cannot be used";
    } finally {
      this.dialogOpen = false;
    }
    await this.refresh();
    this.render();
  }

  private async setRetention(days: number): Promise<void> {
    try {
      await this.deps.core("settings.set", { key: "history_retention", value: days });
      this.retention = days;
    } catch (e) {
      this.error = (e as { message?: string })?.message ?? "Could not save that";
      this.render();
    }
  }
}

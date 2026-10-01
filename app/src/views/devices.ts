import { el } from "../dom";
import { canSwitchPeer } from "../rules";
import type { Connection, Peer, Transfer } from "../types";

/** "+" opens the device list; each row shows a status dot and a Connect button (FR-035). */
export class DevicePanel {
  readonly root = el("section", { class: "panel devices" });
  private open = false;
  private peers: Peer[] = [];
  private connection: Connection = { connected: false, device: null };
  private active: Transfer | null = null;
  private busyId: string | null = null;
  private error = "";

  constructor(
    private onConnect: (deviceId: string) => Promise<void>,
    private onOpen: () => void,
  ) {
    this.render();
  }

  isOpen(): boolean {
    return this.open;
  }

  update(peers: Peer[], connection: Connection, active: Transfer | null): void {
    this.peers = peers;
    this.connection = connection;
    this.active = active;
    this.render();
  }

  private render(): void {
    const plus = el("button", { class: "icon-btn", text: this.open ? "−" : "+", title: "Show nearby devices" });
    plus.addEventListener("click", () => {
      this.open = !this.open;
      if (this.open) this.onOpen();
      this.render();
    });
    const connected = this.connection.device;
    const dest = el(
      "div", { class: "destination" },
      el("span", { class: `dot ${connected?.status === "connected" ? "on" : connected ? "offline" : ""}` }),
      el("span", { text: connected ? `Connected to ${connected.device_name}` : "Not connected" }),
    );
    this.root.replaceChildren(el("div", { class: "panel-head" }, el("h2", { text: "Devices" }), plus), dest);
    if (this.error) this.root.append(el("p", { class: "error", text: this.error }));
    if (!this.open) return;

    const list = el("ul", { class: "device-list" });
    if (this.peers.length === 0) list.append(el("li", { class: "empty", text: "No devices found on this network." }));
    const mayConnect = canSwitchPeer(this.active);
    for (const p of this.peers) {
      const isCurrent = this.connection.device?.device_id === p.device_id && p.status === "connected";
      const btn = el("button", {
        text: isCurrent ? "Connected" : this.busyId === p.device_id ? "Connecting…" : "Connect",
        disabled: isCurrent || !mayConnect || this.busyId !== null,
        title: mayConnect ? "" : "Finish the current transfer before switching devices.",
      });
      btn.addEventListener("click", () => void this.connect(p.device_id));
      list.append(
        el("li", {},
          el("span", { class: `dot ${p.status === "connected" ? "on" : p.status === "offline" ? "offline" : ""}` }),
          el("span", { class: "grow", text: p.device_name }),
          btn),
      );
    }
    this.root.append(list);
  }

  private async connect(deviceId: string): Promise<void> {
    this.busyId = deviceId;
    this.error = "";
    this.render();
    try {
      await this.onConnect(deviceId);
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.busyId = null;
      this.render();
    }
  }
}

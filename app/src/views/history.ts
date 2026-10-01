import { el } from "../dom";
import { formatBytes } from "../rules";
import type { Transfer } from "../types";

function label(t: Transfer): string {
  const peer = t.peer_device_name ?? "unknown device";
  const dir = t.direction === "sent" ? `To ${peer}` : `From ${peer}`;
  return `${dir} · ${t.file_count} file${t.file_count === 1 ? "" : "s"} · ${formatBytes(t.total_size)}`;
}

export function renderActive(t: Transfer | null): HTMLElement {
  if (!t) return el("div", { class: "progress hidden" });
  const pct = t.archive_size ? Math.min(100, Math.round((t.bytes_transferred / t.archive_size) * 100)) : 0;
  const bar = el("div", { class: "bar-fill" });
  bar.style.width = `${pct}%`;
  return el("div", { class: "progress" },
    el("div", { text: `${t.direction === "sent" ? "Sending" : "Receiving"}: ${label(t)}` }),
    el("div", { class: "bar" }, bar),
    el("div", { class: "muted", text: `${t.status} · ${pct}%` }));
}

export function renderHistory(items: Transfer[]): HTMLElement {
  const box = el("section", { class: "panel history" }, el("h2", { text: "History" }));
  if (items.length === 0) {
    box.append(el("p", { class: "empty", text: "No transfers yet." }));
    return box;
  }
  const list = el("ul", { class: "history-list" });
  for (const t of items) {
    const row = el("li", {},
      el("span", { class: `badge ${t.status}`, text: t.status.replace("_", " ") }),
      el("div", { class: "grow" },
        el("div", { text: label(t) }),
        el("div", { class: "muted", text: new Date(t.created_at).toLocaleString() })));
    if (t.error_message) row.querySelector(".grow")?.append(el("div", { class: "error", text: t.error_message }));
    list.append(row);
  }
  box.append(list);
  return box;
}

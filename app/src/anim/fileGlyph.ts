// The little file token that flies into / out of the gateway. Names come from the filesystem or
// the network, so they are only ever set as text.
import { MAX_CHIPS } from "./motion";

const KNOWN = new Set(["txt", "jpg", "jpeg", "png", "pdf"]);

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : "";
}

export function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length ? (parts[parts.length - 1] as string) : path;
}

/** The accent class for a file type (unknown types fall back to a neutral token). */
export function kindOf(name: string): string {
  const ext = extensionOf(name);
  if (ext === "jpeg") return "jpg";
  return KNOWN.has(ext) ? ext : "file";
}

export function shownChips(names: readonly string[]): { shown: string[]; extra: number } {
  const shown = names.slice(0, MAX_CHIPS);
  return { shown, extra: Math.max(0, names.length - shown.length) };
}

export function createChip(name: string, extra = 0): HTMLElement {
  const chip = document.createElement("div");
  chip.className = `chip kind-${kindOf(name)}`;
  const badge = document.createElement("span");
  badge.className = "chip-ext";
  badge.textContent = extensionOf(name).toUpperCase() || "FILE";
  const label = document.createElement("span");
  label.className = "chip-name";
  label.textContent = name;
  chip.append(badge, label);
  if (extra > 0) {
    const more = document.createElement("span");
    more.className = "chip-more";
    more.textContent = `+${extra}`;
    chip.append(more);
  }
  return chip;
}

/** The chips for what the hand holds: up to MAX_CHIPS names, the rest counted from the real total
 * (the core may send fewer names than files). */
export function heldChips(names: readonly string[], count: number): { shown: string[]; extra: number } {
  const shown = names.slice(0, MAX_CHIPS);
  return { shown, extra: Math.max(0, count - shown.length) };
}

/** Vertical offsets that stack `n` chips evenly around the centre line. */
export function stackOffsets(n: number, step: number): number[] {
  return Array.from({ length: Math.max(0, n) }, (_, i) => (i - (n - 1) / 2) * step);
}

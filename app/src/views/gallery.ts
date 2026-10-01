import { el } from "../dom";
import { formatBytes } from "../rules";
import {
  clickItem, normalizeBox, prune, rectSelect, emptySelection,
  type Box, type SelectionState,
} from "../selection";
import type { FileItem } from "../types";

const ICONS: Record<string, string> = { ".jpg": "🖼️", ".mp4": "🎥", ".txt": "📝", ".exe": "⚙️" };

export class Gallery {
  readonly root = el("div", { class: "gallery" });
  private files: FileItem[] = [];
  private state: SelectionState = emptySelection;
  private tiles = new Map<string, HTMLElement>();
  private marquee: HTMLElement | null = null;
  private dragStart: { x: number; y: number; additive: boolean; base: SelectionState } | null = null;

  constructor(private onChange: (selectedIds: string[]) => void) {
    this.root.tabIndex = 0;
    this.root.addEventListener("mousedown", (e) => this.beginDrag(e));
    window.addEventListener("mousemove", (e) => this.moveDrag(e));
    window.addEventListener("mouseup", () => this.endDrag());
  }

  selectedIds(): string[] {
    return [...this.state.selected];
  }

  setFiles(files: FileItem[]): void {
    this.files = files;
    this.state = prune(this.state, files.map((f) => f.id));
    this.render();
    this.onChange(this.selectedIds());
  }

  private order(): string[] {
    return this.files.map((f) => f.id);
  }

  private render(): void {
    this.root.replaceChildren();
    this.tiles.clear();
    if (this.files.length === 0) {
      this.root.append(el("p", { class: "empty", text: "No files yet. Use “Add files” to import some." }));
      return;
    }
    for (const f of this.files) {
      const tile = el(
        "div", { class: "tile", title: `${f.name} · ${formatBytes(f.size)}` },
        el("div", { class: "tile-icon", text: ICONS[f.extension.toLowerCase()] ?? "📄" }),
        el("div", { class: "tile-name", text: f.name }),
      );
      tile.dataset["id"] = f.id;
      tile.classList.toggle("selected", this.state.selected.has(f.id));
      tile.addEventListener("click", (e) => {
        e.stopPropagation();
        this.state = clickItem(this.state, f.id, this.order(), { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey });
        this.paint();
      });
      this.tiles.set(f.id, tile);
      this.root.append(tile);
    }
  }

  private paint(): void {
    for (const [id, tile] of this.tiles) tile.classList.toggle("selected", this.state.selected.has(id));
    this.onChange(this.selectedIds());
  }

  private beginDrag(e: MouseEvent): void {
    if (e.button !== 0 || (e.target as HTMLElement).closest(".tile")) return;
    const additive = e.ctrlKey || e.metaKey;
    this.dragStart = { x: e.clientX, y: e.clientY, additive, base: this.state };
    this.marquee = el("div", { class: "marquee" });
    document.body.append(this.marquee);
    e.preventDefault();
  }

  private moveDrag(e: MouseEvent): void {
    if (!this.dragStart || !this.marquee) return;
    const box = normalizeBox(this.dragStart.x, this.dragStart.y, e.clientX, e.clientY);
    Object.assign(this.marquee.style, {
      left: `${box.left}px`, top: `${box.top}px`,
      width: `${box.right - box.left}px`, height: `${box.bottom - box.top}px`,
    });
    const rects = new Map<string, Box>();
    for (const [id, tile] of this.tiles) {
      const r = tile.getBoundingClientRect();
      rects.set(id, { left: r.left, top: r.top, right: r.right, bottom: r.bottom });
    }
    this.state = rectSelect(this.dragStart.base, box, rects, this.dragStart.additive);
    this.paint();
  }

  private endDrag(): void {
    this.marquee?.remove();
    this.marquee = null;
    this.dragStart = null;
  }
}

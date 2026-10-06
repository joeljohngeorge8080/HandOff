// The only way the UI reaches the core: Tauri command -> sidecar JSON-lines. No peer calls,
// no DB access here (CLAUDE.md non-negotiable #9).
import { invoke } from "@tauri-apps/api/core";
import type { CoreError } from "./types";

export class CoreRequestError extends Error {
  constructor(readonly error: CoreError) {
    super(error.message);
  }
  get code(): string {
    return this.error.code;
  }
}

function asCoreError(e: unknown): CoreError {
  if (typeof e === "object" && e !== null && "code" in e && "message" in e) {
    return e as CoreError;
  }
  return { code: "CORE_UNAVAILABLE", message: String(e) };
}

export async function core<T>(action: string, payload: Record<string, unknown> = {}): Promise<T> {
  try {
    return await invoke<T>("core_request", { action, payload });
  } catch (e) {
    throw new CoreRequestError(asCoreError(e));
  }
}

/** Resize/reposition the edge window (Rust owns the geometry; see src-tauri/src/edge). */
export function setEdgeMode(mode: "idle" | "armed" | "panel" | "stage"): Promise<void> {
  return invoke("edge_set_mode", { mode });
}

export function quitApp(): Promise<void> {
  return invoke("quit_app");
}

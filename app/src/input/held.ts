// Input adapter: what the hand is holding (ADR-060) -> a `held` input for the edge machine.
//
// The core reports only bare file names and a count, never paths, and clears it (count 0) when
// the grab ends. It crosses a process boundary, so it is validated again here.
import type { Input } from "../edge/machine";
import type { Listen } from "./proximity";

const MAX_NAMES = 20;
const MAX_NAME = 120;
const MAX_COUNT = 1000;

export interface Held {
  names: string[];
  count: number;
}

export function parseHeld(data: unknown): Held | null {
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  const { names, count } = d;
  if (!Array.isArray(names) || names.length > MAX_NAMES) return null;
  if (!names.every((n) => typeof n === "string")) return null;
  if (typeof count !== "number" || !Number.isInteger(count) || count < 0 || count > MAX_COUNT) return null;
  return { names: (names as string[]).map((n) => n.slice(0, MAX_NAME)), count };
}

export function attachHeld(listen: Listen, dispatch: (input: Input) => void): Promise<() => void> {
  return listen<{ event?: string; data?: unknown }>("core-event", ({ payload }) => {
    if (payload?.event !== "hand.held") return;
    const held = parseHeld(payload.data);
    if (held) dispatch({ type: "held", names: held.names, count: held.count });
  });
}

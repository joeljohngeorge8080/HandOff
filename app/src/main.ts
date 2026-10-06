// Wires the layers together. No business logic lives here:
//   input adapters -> interaction bus -> edge controller (state machine) -> view/animations
//   core events -> sync -> controller
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { basename } from "./anim/fileGlyph";
import { EdgeController } from "./edge/controller";
import { EdgeView } from "./edge/view";
import { InteractionBus } from "./interaction/bus";
import { attachOsDrag } from "./input/osDrag";
import { attachProximity } from "./input/proximity";
import { core, quitApp, setEdgeMode } from "./ipc";
import { desktopNotify } from "./notifier";
import { Panel } from "./panel/panel";
import { Sync } from "./sync";

/** Dev builds only (stripped from production): forward console output and state changes. */
function devLogging(): void {
  if (!import.meta.env.DEV) return;
  const send = (m: string) => void invoke("ui_log", { message: m }).catch(() => {});
  for (const level of ["log", "warn", "error"] as const) {
    const orig = console[level].bind(console);
    console[level] = (...a: unknown[]) => {
      orig(...a);
      send(`${level}: ${a.map((x) => (x instanceof Error ? x.stack : typeof x === "string" ? x : JSON.stringify(x))).join(" ")}`);
    };
  }
  window.addEventListener("error", (e) => send(`uncaught: ${e.message} @${e.filename}:${e.lineno}`));
  window.addEventListener("unhandledrejection", (e) => send(`unhandled rejection: ${String(e.reason)}`));
  send("webview started");
}

async function boot(): Promise<void> {
  devLogging();
  const view = new EdgeView(document.getElementById("edge") as HTMLElement);
  const bus = new InteractionBus();

  const controller = new EdgeController({
    core,
    setWindowMode: setEdgeMode,
    notify: (title, body) => void desktopNotify(title, body),
    timer: (ms, fn) => {
      const id = setTimeout(fn, ms);
      return () => clearTimeout(id);
    },
  });
  controller.onChange((model, previous) => {
    if (import.meta.env.DEV) console.log(`view ${previous.view.kind} -> ${JSON.stringify(model.view)} peer=${model.peer} busy=${model.busy}`);
    view.render(model, previous);
    if (model.view.kind === "panel" && previous.view.kind !== "panel") panel.show();
    if (model.view.kind !== "panel" && previous.view.kind === "panel") panel.hide();
  });
  controller.attach(bus);

  const panel = new Panel({
    core,
    pickFolder: async (defaultPath) => {
      const picked = await open({ directory: true, multiple: false, defaultPath: defaultPath || undefined });
      return typeof picked === "string" ? picked : null;
    },
    quit: () => void quitApp(),
    onClose: () => controller.dispatch({ type: "close_panel" }),
  });

  // The tokens show what is actually being moved.
  bus.subscribe((e) => {
    if (e.type === "drag_start" || e.type === "release") {
      if (e.paths.length) view.setNames(e.paths.map(basename));
    } else if (e.type === "drag_move") {
      view.pull(e.x, e.y);
    }
  });

  // Clicking the handle (shown after resting at the edge) opens the panel.
  document.addEventListener("click", (ev) => {
    const w = window.innerWidth || 1;
    const h = window.innerHeight || 1;
    bus.emit({ source: "os", type: "pointer_click", x: ev.clientX / w, y: ev.clientY / h });
  });

  if (import.meta.env.DEV) {
    // Dev builds only: log every raw OS drag event, so "the drag never arrived" is visible.
    void getCurrentWebview().onDragDropEvent((e) => {
      const p = e.payload as { type: string; paths?: string[] };
      console.log(`os drag ${p.type} paths=${JSON.stringify(p.paths ?? [])}`);
    });
    bus.subscribe((e) => {
      if (e.type !== "drag_move") console.log(`event ${e.type}`);
    });
    await listen<{ phase?: string }>("edge-proximity", (e) => console.log(`proximity ${e.payload?.phase}`));
    await listen<{ mode?: string; screen?: number; scale?: number }>("edge-mode", (e) =>
      console.log(`window mode ${e.payload?.mode} screen=${e.payload?.screen} scale=${e.payload?.scale}`),
    );
  }

  await attachOsDrag(getCurrentWebview(), bus, () => ({
    width: window.innerWidth * window.devicePixelRatio,
    height: window.innerHeight * window.devicePixelRatio,
  }));
  await attachProximity(listen, (i) => controller.dispatch(i), bus);

  if (import.meta.env.DEV) {
    // Dev builds only: lets a script replay inputs (see src-tauri/src/dev.rs).
    await listen<string>("dev-input", (e) => controller.dispatch(JSON.parse(e.payload)));
  }

  const sync = new Sync({
    core,
    listen,
    dispatch: (i) => controller.dispatch(i),
    notify: (n) => void desktopNotify(n.title, n.body),
    isBusy: () => controller.state.busy,
    onNames: (names) => view.setNames(names),
  });
  await sync.start();
}

void boot().catch((e) => {
  // The edge must never crash silently into an invisible state; a visible hairline remains.
  console.error("HandOff failed to start", e);
});

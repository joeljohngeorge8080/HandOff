//! Applies an edge mode to the real window and exposes the UI-facing commands.
//!
//! One borderless, transparent, always-on-top window (label `edge`) is moved and resized between
//! modes. Platform differences are contained in the Tauri/tao window calls; the geometry itself
//! lives in `geometry.rs`.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::json;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, State};

use super::geometry::{initial_screen, layout, Mode, Rect, Screen};
use super::pointer::button_held;
use super::tracker::{Action, Sample, Tracker};

pub const LABEL: &str = "edge";

/// Width last asked of the window. The window's own size lags right after a resize, so it is
/// not a reliable way to tell whether the next change is a shrink.
static LAST_WIDTH: AtomicU32 = AtomicU32::new(0);
/// The rectangle most recently asked for; stale correction checks compare against it.
static DESIRED: Mutex<Option<Rect>> = Mutex::new(None);

pub struct EdgeState {
    pub tracker: Tracker,
    pub screens: Vec<Screen>,
}

pub struct Edge(pub Mutex<EdgeState>);

pub fn read_screens(app: &AppHandle) -> Vec<Screen> {
    app.available_monitors()
        .unwrap_or_default()
        .iter()
        .map(|m| Screen {
            rect: Rect {
                x: m.position().x,
                y: m.position().y,
                w: m.size().width,
                h: m.size().height,
            },
            scale: m.scale_factor(),
        })
        .collect()
}

fn primary_index(app: &AppHandle, screens: &[Screen]) -> Option<usize> {
    let p = app.primary_monitor().ok().flatten()?;
    screens
        .iter()
        .position(|s| s.rect.x == p.position().x && s.rect.y == p.position().y)
}

/// Size and position the window exactly at `r`.
///
/// Order matters: a window manager keeps a window on screen, so moving a *wide* window to a
/// position where it would hang off the right edge gets clamped. Shrinking therefore resizes
/// first and then moves; growing moves first and then resizes. The result is checked and the
/// move repeated once if the window manager still disagreed.
fn place(win: &tauri::WebviewWindow, r: &Rect) -> tauri::Result<()> {
    let size = PhysicalSize::new(r.w, r.h);
    let pos = PhysicalPosition::new(r.x, r.y);
    let shrinking = r.w < LAST_WIDTH.swap(r.w, Ordering::SeqCst);
    if shrinking {
        win.set_size(size)?;
        win.set_position(pos)?;
    } else {
        win.set_position(pos)?;
        win.set_size(size)?;
    }
    *DESIRED.lock().unwrap_or_else(|e| e.into_inner()) = Some(*r);
    // Window managers apply moves asynchronously, and a quick sequence (arm, then disarm a few
    // milliseconds later) can leave an older move winning. Re-assert the latest request if the
    // window ended up elsewhere. Cheap: two checks, only for the most recent rectangle.
    let (win, r) = (win.clone(), *r);
    std::thread::spawn(move || {
        for wait in [90u64, 250] {
            std::thread::sleep(Duration::from_millis(wait));
            let current = *DESIRED.lock().unwrap_or_else(|e| e.into_inner());
            if current != Some(r) {
                return; // a newer placement owns the window now
            }
            let off = win
                .outer_position()
                .map(|p| p.x != r.x || p.y != r.y)
                .unwrap_or(false);
            if off {
                let _ = win.set_position(PhysicalPosition::new(r.x, r.y));
            }
        }
    });
    Ok(())
}

/// Move/resize the window for `mode` on `screen` and set its input behaviour.
pub fn apply(app: &AppHandle, mode: Mode, screen: &Screen, index: usize) -> tauri::Result<()> {
    let Some(win) = app.get_webview_window(LABEL) else {
        return Ok(());
    };
    let r = layout(screen, mode);
    // Click-through first, so a shrinking window never briefly swallows clicks.
    // Debug builds only: HANDOFF_DEV_HITTABLE=1 never makes the window click-through, to tell a
    // click-through problem from a window that refuses OS drops altogether.
    let always_hit = cfg!(debug_assertions) && std::env::var_os("HANDOFF_DEV_HITTABLE").is_some();
    win.set_ignore_cursor_events(!(mode.hittable() || always_hit))?;
    let _ = win.set_focusable(mode.focusable());
    place(&win, &r)?;
    win.set_always_on_top(true)?;
    if mode.focusable() {
        win.set_focus()?;
    }
    let _ = app.emit(
        "edge-mode",
        json!({"mode": mode, "screen": index, "scale": screen.scale}),
    );
    Ok(())
}

/// Position the window for the first time and make it visible.
///
/// Order matters: click-through (`set_ignore_cursor_events`) needs a realized native window
/// (tao panics otherwise), so the window is sized, shown, and only then switched to Idle.
pub fn start(app: &AppHandle) -> tauri::Result<Edge> {
    let screens = read_screens(app);
    let idx = initial_screen(&screens, primary_index(app, &screens)).unwrap_or(0);
    if let (Some(screen), Some(win)) = (screens.get(idx), app.get_webview_window(LABEL)) {
        let r = layout(screen, Mode::Idle);
        win.set_size(PhysicalSize::new(r.w, r.h))?;
        win.set_position(PhysicalPosition::new(r.x, r.y))?;
        win.set_skip_taskbar(true)?;
        win.show()?;
        apply(app, Mode::Idle, screen, idx)?;
    }
    Ok(Edge(Mutex::new(EdgeState {
        tracker: Tracker::new(Some(idx)),
        screens,
    })))
}

/// UI -> core: switch mode (open the panel, play an animation, go back to idle).
#[tauri::command]
pub fn edge_set_mode(app: AppHandle, edge: State<'_, Edge>, mode: Mode) -> Result<(), String> {
    let mut st = edge
        .0
        .lock()
        .map_err(|_| "edge state poisoned".to_string())?;
    let idx = st
        .tracker
        .screen
        .unwrap_or(0)
        .min(st.screens.len().saturating_sub(1));
    let Some(screen) = st.screens.get(idx).copied() else {
        return Err("no monitor".into());
    };
    apply(&app, mode, &screen, idx).map_err(|e| e.to_string())?;
    st.tracker.external_mode(mode, idx);
    Ok(())
}

#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// Samples the pointer and wakes the edge. Cheap by design: about 7-12 samples a second, a
/// button query only when the pointer is near an edge, and nothing is sent unless state changes.
pub fn spawn_watcher(app: AppHandle) {
    std::thread::spawn(move || {
        let mut ticks = 0u32;
        let mut warned = false;
        loop {
            let calm = {
                let edge = app.state::<Edge>();
                let mut st = edge.0.lock().unwrap_or_else(|e| e.into_inner());
                ticks = ticks.wrapping_add(1);
                if ticks.is_multiple_of(25) {
                    // Monitors change (hotplug, scaling): refresh now and then, not every tick.
                    let screens = read_screens(&app);
                    if !screens.is_empty() {
                        st.screens = screens;
                    }
                }
                if let Ok(c) = app.cursor_position() {
                    let near_any = st.screens.iter().any(|s| {
                        let right = s.rect.right() as f64;
                        c.x >= right - 2.0 * super::geometry::approach_px(s)
                            && c.x < right + 1.0
                            && s.rect.contains(c.x.min(right - 1.0), c.y)
                    });
                    let pressed = if near_any || st.tracker.mode == Mode::Armed {
                        button_held(&app).unwrap_or_else(|| {
                            if !warned {
                                eprintln!(
                                    "handoff: mouse button state unavailable; arming on proximity"
                                );
                                warned = true;
                            }
                            true
                        })
                    } else {
                        false
                    };
                    let screens = st.screens.clone();
                    let sample = Sample {
                        cursor: (c.x, c.y),
                        screens: &screens,
                        pressed,
                        now: Instant::now(),
                    };
                    for action in st.tracker.step(&sample) {
                        match action {
                            Action::SetMode { mode, screen } => {
                                if let Some(scr) = screens.get(screen) {
                                    let _ = apply(&app, mode, scr, screen);
                                }
                            }
                            Action::Proximity(phase) => {
                                let _ = app.emit("edge-proximity", json!({"phase": phase}));
                            }
                        }
                    }
                }
                st.tracker.is_calm()
            };
            std::thread::sleep(Duration::from_millis(if calm { 140 } else { 70 }));
        }
    });
}

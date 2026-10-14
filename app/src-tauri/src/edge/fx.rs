//! The full-screen effects overlay (ADR-059): a transparent, click-through window that plays the
//! hand-gesture animations at the cursor.
//!
//! It exists only while an effect plays. The window is created hidden and parked off-screen at
//! startup; when the core reports `palm_grab` / `palm_release` it is made click-through, moved
//! over the screen the cursor is on, shown, told where to draw, and hidden again afterwards. It
//! never takes focus, never accepts a drop and never receives input, so it cannot get in the way
//! of the edge strip or of a drag.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder,
};

use super::geometry::Screen;
use super::window::read_screens;

pub const LABEL: &str = "fx";
/// The longest effect (ADR-062) is 1.7 s; the window is hidden shortly after.
const HIDE_AFTER_MS: u64 = 2000;
/// Time for the window manager to map the window before the effect starts drawing.
const MAP_DELAY_MS: u64 = 50;
/// Bumped for every effect, so only the newest one gets to hide the window.
static GENERATION: AtomicU64 = AtomicU64::new(0);

/// Create the overlay hidden and parked off-screen. It loads its page now so the first effect
/// does not wait for it.
pub fn create(app: &AppHandle) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("fx.html".into()))
        .title("HandOff effects")
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .focused(false)
        .visible(false)
        .inner_size(64.0, 64.0)
        .position(-32000.0, -32000.0)
        .disable_drag_drop_handler()
        .build()?;
    Ok(())
}

/// Which effect (if any) a core push event asks for. The core event looks like
/// `{"event": "cv.event", "data": {"event": "gesture_detected", "gesture": "palm_grab", ..}}`.
pub fn kind_for(event: &Value) -> Option<&'static str> {
    if event.get("event")?.as_str()? != "cv.event" {
        return None;
    }
    let data = event.get("data")?;
    if data.get("event")?.as_str()? != "gesture_detected" {
        return None;
    }
    match data.get("gesture")?.as_str()? {
        "palm_grab" => Some("grab"),
        "palm_release" => Some("drop"),
        _ => None,
    }
}

/// The screen holding the cursor, or the first one if it is somehow outside them all.
pub fn pick_screen(screens: &[Screen], cursor: (f64, f64)) -> Option<Screen> {
    screens
        .iter()
        .find(|s| s.rect.contains(cursor.0, cursor.1))
        .or_else(|| screens.first())
        .copied()
}

/// The cursor in the overlay's own coordinates: CSS pixels from its top-left corner.
pub fn local_point(cursor: (f64, f64), screen: &Screen) -> (f64, f64) {
    let scale = if screen.scale > 0.0 {
        screen.scale
    } else {
        1.0
    };
    (
        (cursor.0 - screen.rect.x as f64) / scale,
        (cursor.1 - screen.rect.y as f64) / scale,
    )
}

/// Called for every push event from the core, before it is forwarded to the UI.
pub fn on_core_event(app: &AppHandle, event: &Value) {
    if let Some(kind) = kind_for(event) {
        play(app, kind);
    }
}

fn play(app: &AppHandle, kind: &'static str) {
    let app = app.clone();
    // Window calls and a short wait: keep them off the sidecar's reader thread.
    std::thread::spawn(move || {
        let result = play_blocking(&app, kind);
        #[cfg(debug_assertions)]
        eprintln!("[fx] {kind}: {result:?}");
        #[cfg(not(debug_assertions))]
        let _ = result;
    });
}

fn play_blocking(app: &AppHandle, kind: &'static str) -> tauri::Result<()> {
    let Some(win) = app.get_webview_window(LABEL) else {
        return Ok(());
    };
    let Ok(cursor) = app.cursor_position() else {
        return Ok(());
    };
    let cursor = (cursor.x, cursor.y);
    let Some(screen) = pick_screen(&read_screens(app), cursor) else {
        return Ok(());
    };
    let generation = GENERATION.fetch_add(1, Ordering::SeqCst) + 1;

    // Click-through needs a realized native window (see `window::start`), so show it first while
    // it is still small and off-screen, make it transparent to input, and only then cover the
    // screen. It can therefore never swallow a click, even for an instant.
    win.show()?;
    win.set_ignore_cursor_events(true)?;
    let _ = win.set_focusable(false);
    let (pos, size) = (
        PhysicalPosition::new(screen.rect.x, screen.rect.y),
        PhysicalSize::new(screen.rect.w, screen.rect.h),
    );
    win.set_position(pos)?;
    win.set_size(size)?;
    win.set_always_on_top(true)?;
    std::thread::sleep(Duration::from_millis(MAP_DELAY_MS));
    // A window manager may apply the move late; assert it once more if it landed elsewhere.
    if win
        .outer_position()
        .map(|p| p.x != pos.x || p.y != pos.y)
        .unwrap_or(false)
    {
        let _ = win.set_position(pos);
    }

    let (x, y) = local_point(cursor, &screen);
    app.emit_to(LABEL, "fx-play", json!({"kind": kind, "x": x, "y": y}))?;

    std::thread::sleep(Duration::from_millis(HIDE_AFTER_MS));
    if GENERATION.load(Ordering::SeqCst) == generation {
        win.hide()?;
        let _ = win.set_size(PhysicalSize::new(64, 64));
        let _ = win.set_position(PhysicalPosition::new(-32000, -32000));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::edge::geometry::Rect;

    fn screen(x: i32, y: i32, w: u32, h: u32, scale: f64) -> Screen {
        Screen {
            rect: Rect { x, y, w, h },
            scale,
        }
    }

    fn gesture(name: &str) -> Value {
        json!({"event": "cv.event", "data": {"event": "gesture_detected", "gesture": name}})
    }

    #[test]
    fn only_the_two_palm_gestures_start_an_effect() {
        assert_eq!(kind_for(&gesture("palm_grab")), Some("grab"));
        assert_eq!(kind_for(&gesture("palm_release")), Some("drop"));
        for other in [
            "pinch_closed",
            "copied",
            "sent",
            "claimed",
            "palm_grab_x",
            "",
        ] {
            assert_eq!(kind_for(&gesture(other)), None, "{other}");
        }
    }

    #[test]
    fn other_events_and_malformed_ones_never_start_an_effect() {
        let bad = [
            json!(null),
            json!({}),
            json!({"event": "cv.status", "data": {"state": "tracking"}}),
            json!({"event": "cv.event", "data": {"event": "direction_detected", "direction": "left"}}),
            json!({"event": "cv.event", "data": {"event": "gesture_detected", "gesture": 7}}),
            json!({"event": "cv.event"}),
            json!({"event": "transfer.updated", "data": {"gesture": "palm_grab"}}),
        ];
        for e in bad {
            assert_eq!(kind_for(&e), None, "{e}");
        }
    }

    #[test]
    fn the_screen_under_the_cursor_is_chosen() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 0, 2560, 1440, 1.0),
        ];
        assert_eq!(pick_screen(&screens, (100.0, 100.0)), Some(screens[0]));
        assert_eq!(pick_screen(&screens, (2000.0, 50.0)), Some(screens[1]));
    }

    #[test]
    fn a_cursor_outside_every_screen_falls_back_to_the_first() {
        let screens = [screen(0, 0, 1920, 1080, 1.0)];
        assert_eq!(pick_screen(&screens, (-5.0, 9000.0)), Some(screens[0]));
        assert_eq!(pick_screen(&[], (0.0, 0.0)), None);
    }

    #[test]
    fn the_local_point_is_relative_to_the_screen_and_in_css_pixels() {
        let s = screen(1920, 0, 2560, 1440, 2.0);
        assert_eq!(local_point((1920.0 + 400.0, 200.0), &s), (200.0, 100.0));
        let negative = screen(-1920, 0, 1920, 1080, 1.0);
        assert_eq!(local_point((-1820.0, 40.0), &negative), (100.0, 40.0));
    }

    #[test]
    fn a_bad_scale_factor_never_divides_by_zero() {
        let s = screen(0, 0, 100, 100, 0.0);
        let (x, y) = local_point((10.0, 20.0), &s);
        assert!(x.is_finite() && y.is_finite());
    }
}

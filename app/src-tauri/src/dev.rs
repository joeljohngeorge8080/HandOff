//! Debug builds only (`cfg(debug_assertions)`; absent from release builds).
//!
//! `HANDOFF_DEV_SCRIPT=<file>` replays scripted inputs into the real UI so every edge state can
//! be exercised and photographed without driving the mouse. One command per line:
//!
//!   <delay_ms> mode idle|armed|panel|stage      resize the real window
//!   <delay_ms> input {"type":"proximity",...}    feed the edge state machine
//!   <delay_ms> core {"event":"cv.event",...}     push a core event, as the sidecar would
//!
//! Lines starting with `#` are comments.

use std::time::Duration;

use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager};

use crate::edge::geometry::Mode;
use crate::edge::window::{apply, Edge};

pub fn spawn_script(app: AppHandle) {
    let Some(path) = std::env::var_os("HANDOFF_DEV_SCRIPT") else {
        return;
    };
    std::thread::spawn(move || {
        let Ok(text) = std::fs::read_to_string(&path) else {
            eprintln!("[dev] cannot read the script");
            return;
        };
        std::thread::sleep(Duration::from_secs(3)); // let the webview boot
        for line in text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
        {
            let mut parts = line.splitn(3, ' ');
            let delay: u64 = parts.next().and_then(|d| d.parse().ok()).unwrap_or(0);
            let (kind, rest) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
            std::thread::sleep(Duration::from_millis(delay));
            match kind {
                "mode" => {
                    if let Ok(mode) = serde_json::from_value::<Mode>(Value::String(rest.into())) {
                        let edge = app.state::<Edge>();
                        let mut st = edge.0.lock().unwrap_or_else(|e| e.into_inner());
                        let idx = st.tracker.screen.unwrap_or(0);
                        if let Some(screen) = st.screens.get(idx).copied() {
                            let _ = apply(&app, mode, &screen, idx);
                            st.tracker.external_mode(mode, idx);
                        }
                    }
                }
                "input" => {
                    let _ = app.emit("dev-input", rest.to_string());
                }
                "core" => {
                    if let Ok(event) = serde_json::from_str::<Value>(rest) {
                        crate::edge::fx::on_core_event(&app, &event);
                        let _ = app.emit("core-event", event);
                    }
                }
                other => eprintln!("[dev] unknown command {other}"),
            }
        }
        eprintln!("[dev] script finished");
    });
}

#[cfg(debug_assertions)]
mod dev;
mod edge;
mod sidecar;

use serde_json::Value;
use sidecar::Sidecar;
use std::sync::Arc;
use tauri::{Emitter, Manager, RunEvent, State};

struct Core(Arc<Sidecar>);

/// The only bridge from the UI to the core. Runs off the main thread so the UI never blocks.
#[tauri::command]
async fn core_request(
    core: State<'_, Core>,
    action: String,
    payload: Value,
) -> Result<Value, Value> {
    let sidecar = Arc::clone(&core.0);
    tauri::async_runtime::spawn_blocking(move || sidecar.request(&action, payload))
        .await
        .map_err(|e| serde_json::json!({"code": "INTERNAL_ERROR", "message": e.to_string()}))?
}

/// Dev aid: the webview's console and state changes show up in the terminal. A no-op in release.
#[tauri::command]
fn ui_log(message: String) {
    #[cfg(debug_assertions)]
    eprintln!("[ui] {message}");
    #[cfg(not(debug_assertions))]
    let _ = message;
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let sidecar = Sidecar::spawn(app.path().resource_dir().ok(), move |event| {
                let _ = handle.emit("core-event", event);
            })
            .map_err(|e| e.to_string())?;
            app.manage(Core(Arc::new(sidecar)));
            let edge_state = edge::window::start(app.handle())?;
            app.manage(edge_state);
            edge::window::spawn_watcher(app.handle().clone());
            #[cfg(debug_assertions)]
            dev::spawn_script(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            core_request,
            ui_log,
            edge::window::edge_set_mode,
            edge::window::quit_app
        ])
        .build(tauri::generate_context!())
        .expect("error while building HandOff");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(core) = handle.try_state::<Core>() {
                core.0.shutdown();
            }
        }
    });
}

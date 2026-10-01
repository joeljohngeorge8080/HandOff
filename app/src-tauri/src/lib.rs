mod sidecar;

use serde_json::Value;
use sidecar::Sidecar;
use std::sync::Arc;
use tauri::{Manager, RunEvent, State};

struct Core(Arc<Sidecar>);

/// The only bridge from the UI to the core. Runs off the main thread so the UI never blocks.
#[tauri::command]
async fn core_request(core: State<'_, Core>, action: String, payload: Value) -> Result<Value, Value> {
    let sidecar = Arc::clone(&core.0);
    tauri::async_runtime::spawn_blocking(move || sidecar.request(&action, payload))
        .await
        .map_err(|e| serde_json::json!({"code": "INTERNAL_ERROR", "message": e.to_string()}))?
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let sidecar = Sidecar::spawn().map_err(|e| e.to_string())?;
            app.manage(Core(Arc::new(sidecar)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_request])
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

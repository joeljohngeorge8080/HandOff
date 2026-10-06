//! Whether a mouse button is held down, anywhere on screen (including during an OS drag).
//!
//! The watcher needs this to tell "dragging a file toward the edge" from "just moving the
//! pointer there". Tauri exposes the cursor position but not the button state, hence the small
//! per-platform shims. `None` means "unknown on this platform".

use tauri::AppHandle;

#[cfg(target_os = "linux")]
pub fn button_held(app: &AppHandle) -> Option<bool> {
    use gdk::prelude::*;
    use std::sync::mpsc::channel;
    use std::time::Duration;

    // GDK is not thread-safe: ask the main thread.
    let (tx, rx) = channel();
    app.run_on_main_thread(move || {
        let held = (|| {
            let display = gdk::Display::default()?;
            let pointer = display.default_seat()?.pointer()?;
            let root = display.default_screen().root_window()?;
            let (_, _, _, mask) = root.device_position(&pointer);
            Some(mask.intersects(gdk::ModifierType::BUTTON1_MASK | gdk::ModifierType::BUTTON3_MASK))
        })();
        let _ = tx.send(held);
    })
    .ok()?;
    rx.recv_timeout(Duration::from_millis(100)).ok().flatten()
}

#[cfg(windows)]
pub fn button_held(_app: &AppHandle) -> Option<bool> {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, VK_LBUTTON, VK_RBUTTON,
    };
    // Explorer starts a file drag with the left button (or the right one, which asks what to do
    // on drop). The high bit means "currently down".
    // SAFETY: GetAsyncKeyState has no preconditions.
    let down = |vk: u16| unsafe { GetAsyncKeyState(vk as i32) } < 0;
    Some(down(VK_LBUTTON) || down(VK_RBUTTON))
}

#[cfg(not(any(target_os = "linux", windows)))]
pub fn button_held(_app: &AppHandle) -> Option<bool> {
    None
}

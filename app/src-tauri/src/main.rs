#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // A global always-on-top strip pinned to the screen edge needs absolute window positioning,
    // which Wayland does not allow. Use XWayland there (ADR-054). Must run before GTK starts.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WAYLAND_DISPLAY").is_some()
        && std::env::var_os("GDK_BACKEND").is_none()
        && std::env::var_os("DISPLAY").is_some()
    {
        std::env::set_var("GDK_BACKEND", "x11");
    }
    handoff_lib::run()
}

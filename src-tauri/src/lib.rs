pub mod driver;
pub mod rpc;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            #[cfg(desktop)]
            {
                let handle = app.handle();
                if let Err(error) = handle.plugin(tauri_plugin_process::init()) {
                    eprintln!("restarting the app from inside is unavailable: {error}");
                }
                if let Err(error) = handle.plugin(tauri_plugin_updater::Builder::new().build()) {
                    eprintln!("update checks are unavailable: {error}");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![rpc::rpc])
        .run(tauri::generate_context!())
        .expect("error while running sptmod");
}

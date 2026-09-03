pub mod driver;
pub mod rpc;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![rpc::rpc])
        .run(tauri::generate_context!())
        .expect("error while running sptmod");
}

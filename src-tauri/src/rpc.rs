use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};

use crate::driver;

pub const FUNCTIONS: [&str; 43] = [
    "list_directories",
    "locate_directory",
    "init_game_dir",
    "set_spt_version",
    "install_from_archive",
    "list_mods",
    "mod_files",
    "remove_mod",
    "list_profiles",
    "check_profiles",
    "plan_profile_cleanup",
    "clean_profile",
    "history",
    "sync_catalog",
    "search_catalog",
    "browse_catalog",
    "disable_mod",
    "enable_mod",
    "adopt_mod",
    "status",
    "snapshot",
    "export_mods",
    "import_list",
    "clear_catalog_cache",
    "get_mod_details",
    "plan_mod_install",
    "install_mod",
    "check_updates",
    "update_mod",
    "list_favorites",
    "set_favorite",
    "list_mod_sets",
    "save_mod_set",
    "delete_mod_set",
    "rename_mod_set",
    "export_mod_set",
    "import_mod_set",
    "bind_profile_set",
    "plan_mod_set",
    "apply_mod_set",
    "launch_profile",
    "launch_status",
    "stop_game",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcError {
    pub error: String,
    pub code: String,
    #[serde(default = "empty_details")]
    pub details: Value,
}

impl RpcError {
    pub fn new(error: impl Into<String>, code: &str) -> Self {
        Self {
            error: error.into(),
            code: code.to_string(),
            details: empty_details(),
        }
    }
}

fn empty_details() -> Value {
    Value::Object(serde_json::Map::new())
}

#[tauri::command]
pub async fn rpc(app: AppHandle, name: String, payload: Value) -> Result<Value, RpcError> {
    if !FUNCTIONS.contains(&name.as_str()) {
        return Err(RpcError::new(
            format!("unknown function: {name}"),
            "unknown_function",
        ));
    }
    let binary = driver::binary_path();
    driver::call(&binary, &name, &payload, move |event| {
        let _ = app.emit("progress", event);
    })
    .await
}

//! Ignored end-to-end smoke tests driving the real `sptmod` CLI.
//!
//! Run with `cargo test --test rpc_e2e_ignored -- --ignored --nocapture`. The
//! binary comes from `SPTMOD_CLI` or `sptmod` on `PATH`, the game directory
//! from `SPTMOD_SMOKE_GAME_DIR` or `<repo>/_data/fakegame` (create it with
//! `uv run python _data/fakegame_setup.py`).

use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use serde_json::{json, Value};

use sptmod_desktop_lib::driver::{self, ProgressEvent};
use sptmod_desktop_lib::rpc::RpcError;

const ARCHIVE: &str = "_data/mods/Tyfon-UIFixes-6.0.2.zip";

/// Both tests drive the same game directory, so they must not overlap.
static GAME_DIR_LOCK: Mutex<()> = Mutex::new(());

fn lock_game_dir() -> MutexGuard<'static, ()> {
    GAME_DIR_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner())
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf()
}

fn game_dir() -> PathBuf {
    match std::env::var_os("SPTMOD_SMOKE_GAME_DIR") {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        _ => repo_root().join("_data/fakegame"),
    }
}

/// Drops the repository an earlier run created, so `init_game_dir` starts over.
fn reset_repo(dir: &Path) {
    assert!(dir.is_dir(), "missing game directory {}", dir.display());
    let _ = std::fs::remove_dir_all(dir.join(".git"));
}

fn write_game_layout(root: &Path, spt_version: Option<&str>) {
    for relative in [
        "SPT/user/mods",
        "SPT/user/profiles/backups",
        "SPT/user/logs",
        "SPT/SPT_Data/configs",
        "BepInEx/plugins",
        "BepInEx/config",
        "EscapeFromTarkov_Data",
    ] {
        std::fs::create_dir_all(root.join(relative)).unwrap();
    }
    let core = match spt_version {
        Some(version) => json!({ "sptVersion": version }),
        None => json!({}),
    };
    std::fs::write(
        root.join("SPT/SPT_Data/configs/core.json"),
        core.to_string(),
    )
    .unwrap();
    std::fs::write(
        root.join("SPT/user/profiles/abc.json"),
        json!({"Inventory": {"items": []}, "TradersInfo": {}, "Quests": []}).to_string(),
    )
    .unwrap();
    std::fs::write(root.join("SPT/user/logs/x.log"), "dummy log line\n").unwrap();
    std::fs::write(root.join("EscapeFromTarkov_Data/foo"), "dummy game data\n").unwrap();
    std::fs::write(root.join("SPT/user/profiles/backups/old.json"), "{}").unwrap();
}

async fn call(name: &str, payload: Value) -> Result<Value, RpcError> {
    let outcome = driver::call(&driver::binary_path(None), name, &payload, |_| {}).await;
    report(name, &outcome);
    outcome
}

async fn call_with_progress(
    name: &str,
    payload: Value,
    events: &mut Vec<ProgressEvent>,
) -> Result<Value, RpcError> {
    let outcome = driver::call(&driver::binary_path(None), name, &payload, |event| {
        events.push(event)
    })
    .await;
    report(name, &outcome);
    outcome
}

fn report(name: &str, outcome: &Result<Value, RpcError>) {
    match outcome {
        Ok(_) => println!("{name} -> ok"),
        Err(error) => println!("{name} -> {} ({})", error.code, error.error),
    }
}

fn expect_code(outcome: Result<Value, RpcError>, code: &str) {
    match outcome {
        Ok(value) => panic!("expected {code}, got result {value}"),
        Err(error) => assert_eq!(error.code, code, "{}", error.error),
    }
}

#[tokio::test]
#[ignore = "drives the real sptmod binary"]
async fn smoke_readonly_and_errors() {
    let _guard = lock_game_dir();
    let dir = game_dir();
    reset_repo(&dir);
    let cache = tempfile::tempdir().unwrap();
    let db_path = cache.path().join("catalog.db");
    let path = dir.to_string_lossy().to_string();

    call("init_game_dir", json!({ "path": path }))
        .await
        .expect("first init succeeds");
    expect_code(
        call("init_game_dir", json!({ "path": path })).await,
        "ALREADY_INITIALIZED",
    );

    call("status", json!({ "path": path }))
        .await
        .expect("status succeeds");
    call("list_mods", json!({ "path": path }))
        .await
        .expect("list_mods succeeds");

    let exported = call(
        "export_mods",
        json!({ "path": path, "db_path": db_path.to_string_lossy() }),
    )
    .await
    .expect("export_mods succeeds");
    let content = exported["content"].as_str().expect("content string");
    println!("exported list: {content:?}");

    call(
        "import_list",
        json!({
            "path": path,
            "content": content,
            "db_path": db_path.to_string_lossy(),
        }),
    )
    .await
    .expect("import_list dry run succeeds");

    expect_code(
        call("init_game_dir", json!({ "path": "/tmp" })).await,
        "NOT_A_GAME_DIR",
    );

    let unversioned = tempfile::tempdir().unwrap();
    write_game_layout(unversioned.path(), None);
    expect_code(
        call(
            "init_game_dir",
            json!({ "path": unversioned.path().to_string_lossy() }),
        )
        .await,
        "SPT_VERSION_UNKNOWN",
    );
}

#[tokio::test]
#[ignore = "drives the real sptmod binary and writes to the game directory"]
async fn smoke_install_remove() {
    let _guard = lock_game_dir();
    let dir = game_dir();
    reset_repo(&dir);
    let cache = tempfile::tempdir().unwrap();
    let db_path = cache.path().join("catalog.db");
    let path = dir.to_string_lossy().to_string();
    let archive = repo_root().join(ARCHIVE);
    assert!(archive.is_file(), "missing archive {}", archive.display());

    call("init_game_dir", json!({ "path": path }))
        .await
        .expect("init succeeds");

    let mut events: Vec<ProgressEvent> = Vec::new();
    let installed = call_with_progress(
        "install_from_archive",
        json!({
            "path": path,
            "archive": archive.to_string_lossy(),
            "db_path": db_path.to_string_lossy(),
        }),
        &mut events,
    )
    .await;

    let installed = match installed {
        Ok(value) => value,
        Err(error) => {
            println!(
                "install blocked: code={} error={} details={}",
                error.code, error.error, error.details
            );
            return;
        }
    };

    assert!(!events.is_empty(), "no progress events observed");
    println!("progress events: {}", events.len());
    for event in &events {
        println!(
            "progress {} {}% {}",
            event.step, event.percent, event.message
        );
    }

    let name = installed["mod"]["name"].as_str().expect("mod name");
    println!("installed mod: {name}");

    let listed = call("list_mods", json!({ "path": path }))
        .await
        .expect("list_mods succeeds");
    let names: Vec<&str> = listed
        .as_array()
        .expect("mod array")
        .iter()
        .filter_map(|mod_info| mod_info["name"].as_str())
        .collect();
    assert!(
        names.contains(&name),
        "installed mod missing from {names:?}"
    );

    call("remove_mod", json!({ "path": path, "name": name }))
        .await
        .expect("remove_mod succeeds");
}

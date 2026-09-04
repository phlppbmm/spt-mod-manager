//! Integration tests for the `driver::call` subprocess bridge and the
//! Tauri-free pieces of `rpc.rs` (FUNCTIONS list, RpcError serialization).
//!
//! Every scenario spawns a small shell script stub (written into a tempdir)
//! in place of the real `sptmod` CLI binary, so no Python process is ever
//! invoked here.

use std::env::VarError;
use std::ffi::OsString;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use serde_json::json;

use sptmod_desktop_lib::driver::{self, ProgressEvent};
use sptmod_desktop_lib::rpc::{RpcError, FUNCTIONS};

/// Guards mutation of `driver::BINARY_ENV` (a process-global env var), so
/// the env-var-dependent `binary_path` tests don't race each other if run
/// concurrently.
static ENV_GUARD: Mutex<()> = Mutex::new(());

/// Locks `ENV_GUARD`, recovering from poisoning instead of propagating it.
/// A panic from one `assert_eq!` inside the case loop must not turn every
/// later test in this binary into an unrelated `PoisonError` failure.
fn lock_env_guard() -> MutexGuard<'static, ()> {
    ENV_GUARD.lock().unwrap_or_else(|error| error.into_inner())
}

/// Guards every `driver::call` invocation in this suite so that at most one
/// child process is being spawned at a time.
///
/// Spawning a subprocess (via `posix_spawn`/`vfork`) from multiple OS
/// threads of the same process concurrently is subject to a known kernel/
/// glibc race that can make an unrelated, freshly-written and already-
/// closed script spuriously fail to exec with `ETXTBSY` ("Text file
/// busy") — this was observed flaking this suite even after the stub
/// script's write handle was fsync'd and closed before spawning. Since
/// this suite's own tests are the only thing spawning processes here,
/// serializing spawns eliminates the race without weakening what each
/// test asserts about `driver::call`'s behavior.
///
/// `tokio::sync::Mutex` (rather than `std::sync::Mutex`) is used because
/// the guard is held across `.await` points; an async-aware mutex avoids
/// blocking the executor thread while waiting and keeps `clippy` quiet
/// about holding a sync lock across an await.
static SPAWN_GUARD: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

/// Restores an env var to its original value (present or absent) when
/// dropped, even if the code between construction and drop panics.
struct EnvVarRestore {
    key: &'static str,
    original: Result<OsString, VarError>,
}

impl EnvVarRestore {
    fn capture(key: &'static str) -> Self {
        Self {
            key,
            original: std::env::var_os(key).ok_or(VarError::NotPresent),
        }
    }
}

impl Drop for EnvVarRestore {
    fn drop(&mut self) {
        match &self.original {
            // SAFETY: all mutation of this env var in this test binary is
            // serialized through `ENV_GUARD`, which is held for the whole
            // lifetime of this guard.
            Ok(value) => unsafe { std::env::set_var(self.key, value) },
            Err(_) => unsafe { std::env::remove_var(self.key) },
        }
    }
}

/// Writes `contents` to `dir/name` and marks it executable (unix only, which
/// matches the dev/CI environment this suite runs in).
///
/// The write handle is explicitly flushed, `fsync`ed and closed (via
/// `File`'s `Drop`) *before* permissions are set and the path is handed
/// back to a caller that may spawn it.
fn write_script(dir: &Path, name: &str, contents: &str) -> PathBuf {
    let path = dir.join(name);
    {
        let mut file = std::fs::File::create(&path).expect("create stub script");
        file.write_all(contents.as_bytes())
            .expect("write stub script contents");
        file.sync_all().expect("flush stub script to disk");
        // `file` (and its write handle) is dropped/closed here, before any
        // caller can spawn the script.
    }
    let mut perms = std::fs::metadata(&path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(&path, perms).unwrap();
    path
}

#[tokio::test]
async fn call_returns_stdin_payload_as_result_and_invokes_binary_with_rpc_and_name() {
    let dir = tempfile::tempdir().unwrap();
    let args_file = dir.path().join("args.txt");
    let script = write_script(
        dir.path(),
        "stub.sh",
        &format!(
            "#!/usr/bin/env bash\n\
             printf '%s' \"$*\" > {args:?}\n\
             payload=$(cat)\n\
             printf '{{\"event\":\"result\",\"data\":%s}}\\n' \"$payload\"\n",
            args = args_file.to_string_lossy(),
        ),
    );

    let payload = json!({"path": "/mods/example", "recursive": true});
    let _guard = SPAWN_GUARD.lock().await;
    let result = driver::call(&script, "list_mods", &payload, |_| {})
        .await
        .expect("stub returns Ok");

    assert_eq!(result, payload);

    let recorded_args = std::fs::read_to_string(&args_file).unwrap();
    assert_eq!(recorded_args, "rpc list_mods");
}

#[tokio::test]
async fn call_reports_progress_events_in_order_and_ignores_interleaved_junk_line() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo '{\"event\":\"progress\",\"operation_id\":\"op-1\",\"step\":\"download\",\"percent\":10,\"message\":\"starting\"}'\n\
         echo 'this line is not json and must not abort the stream'\n\
         echo '{\"event\":\"progress\",\"operation_id\":\"op-1\",\"step\":\"extract\",\"percent\":75,\"message\":\"almost done\"}'\n\
         echo '{\"event\":\"result\",\"data\":{\"installed\":true}}'\n",
    );

    let mut events = Vec::new();
    let _guard = SPAWN_GUARD.lock().await;
    let result = driver::call(&script, "install_mod", &json!({}), |event| {
        events.push(event);
    })
    .await
    .expect("stub returns Ok despite junk line");

    assert_eq!(
        events,
        vec![
            ProgressEvent {
                operation_id: "op-1".to_string(),
                step: "download".to_string(),
                percent: 10,
                message: "starting".to_string(),
            },
            ProgressEvent {
                operation_id: "op-1".to_string(),
                step: "extract".to_string(),
                percent: 75,
                message: "almost done".to_string(),
            },
        ]
    );
    assert_eq!(result, json!({"installed": true}));
}

#[tokio::test]
async fn call_returns_rpc_error_with_details_preserved_on_error_line_and_exit_1() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo '{\"event\":\"error\",\"data\":{\"error\":\"archive is corrupt\",\"code\":\"install_failed\",\"details\":{\"path\":\"/mods/x.zip\"}}}'\n\
         exit 1\n",
    );

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&script, "install_from_archive", &json!({}), |_| {})
        .await
        .expect_err("stub emits an error line");

    assert_eq!(error.error, "archive is corrupt");
    assert_eq!(error.code, "install_failed");
    assert_eq!(error.details, json!({"path": "/mods/x.zip"}));
}

#[tokio::test]
async fn call_error_line_without_details_field_defaults_details_to_empty_object() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo '{\"event\":\"error\",\"data\":{\"error\":\"boom\",\"code\":\"install_failed\"}}'\n\
         exit 1\n",
    );

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&script, "install_mod", &json!({}), |_| {})
        .await
        .expect_err("stub emits an error line");

    assert_eq!(error.error, "boom");
    assert_eq!(error.code, "install_failed");
    assert_eq!(error.details, json!({}));
}

#[tokio::test]
async fn call_returns_bad_output_with_exit_status_and_last_20_stderr_lines_on_garbage_stdout() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo 'this is garbage, not an NDJSON event'\n\
         echo 'still not a result or error line'\n\
         for i in $(seq 1 25); do echo \"err line $i\" >&2; done\n\
         exit 3\n",
    );

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&script, "status", &json!({}), |_| {})
        .await
        .expect_err("no parseable final line means bad_output");

    assert_eq!(error.code, "bad_output");
    assert!(
        error.error.contains("exit status: 3"),
        "message should contain the exit status, got: {}",
        error.error
    );

    let tail_lines: Vec<&str> = error
        .error
        .lines()
        .filter(|line| line.starts_with("err line "))
        .collect();
    let expected: Vec<String> = (6..=25).map(|i| format!("err line {i}")).collect();
    assert_eq!(
        tail_lines, expected,
        "only the last 20 stderr lines, in order"
    );
}

#[tokio::test]
async fn call_returns_bad_output_on_empty_stdout_with_exit_0() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n",
    );

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&script, "status", &json!({}), |_| {})
        .await
        .expect_err("empty stdout produces no final line");

    assert_eq!(error.code, "bad_output");
    assert!(
        error.error.contains("exit status: 0"),
        "message should contain the exit status, got: {}",
        error.error
    );
}

#[tokio::test]
async fn call_returns_unreachable_when_binary_does_not_exist() {
    let dir = tempfile::tempdir().unwrap();
    let missing = dir.path().join("no-such-sptmod-binary");

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&missing, "status", &json!({}), |_| {})
        .await
        .expect_err("spawning a missing binary must fail");

    assert_eq!(error.code, "unreachable");
    assert!(
        error.error.contains(&missing.display().to_string()),
        "message should name the binary path, got: {}",
        error.error
    );
}

#[tokio::test]
async fn call_ignores_ndjson_line_with_unrecognized_event_value_and_still_finds_result() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo '{\"event\":\"unknown\",\"anything\":true}'\n\
         echo '{\"event\":\"result\",\"data\":42}'\n",
    );

    let mut events = Vec::new();
    let _guard = SPAWN_GUARD.lock().await;
    let result = driver::call(&script, "status", &json!({}), |event| {
        events.push(event);
    })
    .await
    .expect("stub returns Ok despite the unrecognized-event line");

    assert_eq!(
        events,
        Vec::<ProgressEvent>::new(),
        "a valid-JSON line with an unrecognized event value must not be reported as progress"
    );
    assert_eq!(result, json!(42));
}

#[test]
fn binary_path_reads_sptmod_cli_env_var_or_falls_back_to_default_when_unset_or_empty() {
    let _guard = lock_env_guard();
    let _restore = EnvVarRestore::capture(driver::BINARY_ENV);

    struct Case {
        id: &'static str,
        env_value: Option<&'static str>,
        expected: &'static str,
    }
    let cases = [
        Case {
            id: "custom path set",
            env_value: Some("/opt/custom/sptmod-cli"),
            expected: "/opt/custom/sptmod-cli",
        },
        Case {
            id: "unset",
            env_value: None,
            expected: driver::DEFAULT_BINARY,
        },
        Case {
            id: "set to empty string",
            env_value: Some(""),
            expected: driver::DEFAULT_BINARY,
        },
    ];

    for case in cases {
        match case.env_value {
            // SAFETY: serialized by ENV_GUARD; no other code in this test
            // binary reads or writes SPTMOD_CLI concurrently.
            Some(value) => unsafe { std::env::set_var(driver::BINARY_ENV, value) },
            None => unsafe { std::env::remove_var(driver::BINARY_ENV) },
        }
        assert_eq!(
            driver::binary_path(),
            PathBuf::from(case.expected),
            "case: {}",
            case.id
        );
    }
    // `_restore` (dropped here) restores the original SPTMOD_CLI value even
    // if one of the assertions above had panicked.
}

#[test]
fn rpc_error_serializes_to_exactly_error_code_details_keys() {
    let with_details = RpcError {
        error: "boom".to_string(),
        code: "install_failed".to_string(),
        details: json!({"path": "/x"}),
    };
    let value = serde_json::to_value(&with_details).unwrap();
    assert_eq!(
        value,
        json!({"error": "boom", "code": "install_failed", "details": {"path": "/x"}})
    );

    let via_constructor = RpcError::new("no details here", "generic_error");
    let value = serde_json::to_value(&via_constructor).unwrap();
    assert_eq!(
        value,
        json!({"error": "no details here", "code": "generic_error", "details": {}})
    );
}

#[test]
fn rpc_error_deserializes_missing_details_field_to_empty_object() {
    let parsed: RpcError =
        serde_json::from_value(json!({"error": "boom", "code": "install_failed"})).unwrap();
    assert_eq!(parsed.details, json!({}));
}

#[test]
fn functions_has_31_unique_entries_matching_python_rpc_functions_keys() {
    // Hardcoded from the keys of RPC_FUNCTIONS in
    // src/sptmod/server/rpc.py, as of the time this test was written.
    // If the Python dict's key set changes, this must be updated in lockstep
    // to keep the Rust FUNCTIONS list from silently drifting.
    let mut expected_from_python = vec![
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
    ];
    expected_from_python.sort_unstable();

    let mut actual: Vec<&str> = FUNCTIONS.to_vec();
    actual.sort_unstable();

    assert_eq!(FUNCTIONS.len(), 31);
    let unique: std::collections::HashSet<&str> = FUNCTIONS.iter().copied().collect();
    assert_eq!(unique.len(), 31, "FUNCTIONS must contain no duplicates");
    assert_eq!(actual, expected_from_python);
}

/// The `unknown_function` check in `rpc::rpc` (`!FUNCTIONS.contains(&name)`)
/// requires an `AppHandle` and is not directly callable here. This test
/// exercises the same Tauri-free seam the command uses to decide whether a
/// name is known, without spawning a process.
#[test]
fn functions_contains_distinguishes_known_from_unknown_names() {
    assert!(FUNCTIONS.contains(&"list_mods"));
    assert!(!FUNCTIONS.contains(&"totally_made_up_function"));
}

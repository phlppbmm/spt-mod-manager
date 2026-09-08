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
async fn call_maps_an_error_line_to_an_rpc_error_carrying_its_details() {
    struct Case {
        id: &'static str,
        data: &'static str,
        expected_details: serde_json::Value,
    }
    let cases = [
        Case {
            id: "details preserved",
            data: r#"{"error":"boom","code":"install_failed","details":{"path":"/mods/x.zip"}}"#,
            expected_details: json!({"path": "/mods/x.zip"}),
        },
        Case {
            id: "details missing defaults to {}",
            data: r#"{"error":"boom","code":"install_failed"}"#,
            expected_details: json!({}),
        },
    ];

    for case in cases {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(
            dir.path(),
            "stub.sh",
            &format!(
                "#!/usr/bin/env bash\ncat >/dev/null\necho '{{\"event\":\"error\",\"data\":{}}}'\nexit 1\n",
                case.data
            ),
        );

        let _guard = SPAWN_GUARD.lock().await;
        let error = driver::call(&script, "install_from_archive", &json!({}), |_| {})
            .await
            .expect_err("stub emits an error line");

        assert_eq!(error.error, "boom", "case: {}", case.id);
        assert_eq!(error.code, "install_failed", "case: {}", case.id);
        assert_eq!(error.details, case.expected_details, "case: {}", case.id);
    }
}

#[tokio::test]
async fn call_without_a_parseable_final_line_reports_bad_output_with_the_exit_status() {
    struct Case {
        id: &'static str,
        body: &'static str,
        status: i32,
    }
    let cases = [
        Case {
            id: "garbage stdout, exit 3",
            body: "echo 'this is garbage, not an NDJSON event'\n\
                   echo 'still not a result or error line'\n\
                   exit 3\n",
            status: 3,
        },
        Case {
            id: "empty stdout, exit 0",
            body: "exit 0\n",
            status: 0,
        },
    ];

    for case in cases {
        let dir = tempfile::tempdir().unwrap();
        let script = write_script(
            dir.path(),
            "stub.sh",
            &format!("#!/usr/bin/env bash\ncat >/dev/null\n{}", case.body),
        );

        let _guard = SPAWN_GUARD.lock().await;
        let error = driver::call(&script, "status", &json!({}), |_| {})
            .await
            .expect_err("no parseable final line means bad_output");

        assert_eq!(error.code, "bad_output", "case: {}", case.id);
        assert!(
            error
                .error
                .contains(&format!("exit status: {}", case.status)),
            "case: {}, message should contain the exit status, got: {}",
            case.id,
            error.error
        );
    }
}

#[tokio::test]
async fn call_reports_only_the_last_20_stderr_lines_of_a_failing_child() {
    let dir = tempfile::tempdir().unwrap();
    let script = write_script(
        dir.path(),
        "stub.sh",
        "#!/usr/bin/env bash\n\
         cat >/dev/null\n\
         echo 'this is garbage, not an NDJSON event'\n\
         for i in $(seq 1 25); do echo \"err line $i\" >&2; done\n\
         exit 3\n",
    );

    let _guard = SPAWN_GUARD.lock().await;
    let error = driver::call(&script, "status", &json!({}), |_| {})
        .await
        .expect_err("no parseable final line means bad_output");

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
            driver::binary_path(None),
            PathBuf::from(case.expected),
            "case: {}",
            case.id
        );
    }
    // `_restore` (dropped here) restores the original SPTMOD_CLI value even
    // if one of the assertions above had panicked.
}

#[test]
fn resolve_binary_resolution_order_across_env_bundled_and_exists() {
    struct Case {
        id: &'static str,
        env_value: Option<&'static str>,
        bundled: Option<&'static str>,
        exists_returns: bool,
        expected: &'static str,
    }
    let cases = [
        Case {
            id: "non-empty env override wins even when the bundled sidecar exists",
            env_value: Some("/opt/custom/sptmod-cli"),
            bundled: Some("/resources/sidecar/sptmod"),
            exists_returns: true,
            expected: "/opt/custom/sptmod-cli",
        },
        Case {
            id: "non-empty env override wins when there is no bundled sidecar",
            env_value: Some("/opt/custom/sptmod-cli"),
            bundled: None,
            exists_returns: false,
            expected: "/opt/custom/sptmod-cli",
        },
        Case {
            id: "empty env override is ignored, falls through to the existing bundled sidecar",
            env_value: Some(""),
            bundled: Some("/resources/sidecar/sptmod"),
            exists_returns: true,
            expected: "/resources/sidecar/sptmod",
        },
        Case {
            id: "unset env uses the bundled sidecar when it exists",
            env_value: None,
            bundled: Some("/resources/sidecar/sptmod"),
            exists_returns: true,
            expected: "/resources/sidecar/sptmod",
        },
        Case {
            id: "unset env falls back to PATH default when the bundled file is missing",
            env_value: None,
            bundled: Some("/resources/sidecar/sptmod"),
            exists_returns: false,
            expected: driver::DEFAULT_BINARY,
        },
        Case {
            id: "empty env override with no bundled path known falls back to PATH default",
            env_value: Some(""),
            bundled: None,
            exists_returns: false,
            expected: driver::DEFAULT_BINARY,
        },
    ];

    for case in cases {
        let env_value = case.env_value.map(OsString::from);
        let bundled = case.bundled.map(PathBuf::from);
        let result = driver::resolve_binary(env_value, bundled, &|_path| case.exists_returns);
        assert_eq!(result, PathBuf::from(case.expected), "case: {}", case.id);
    }
}

/// `resolve_binary` must short-circuit before probing the filesystem when it
/// has no bundled path to probe: this pins that `exists` is only ever called
/// with a `Some(bundled)` path, never invented from thin air.
#[test]
fn resolve_binary_does_not_probe_exists_when_no_bundled_path_is_known() {
    let result = driver::resolve_binary(None, None, &|_path| {
        panic!("exists() must not be called when there is no bundled path")
    });
    assert_eq!(result, PathBuf::from(driver::DEFAULT_BINARY));
}

#[test]
fn sidecar_path_joins_sidecar_subdir_and_binary_name_with_platform_exe_suffix() {
    let resource_dir = Path::new("/resources");
    let result = driver::sidecar_path(resource_dir);
    let expected = Path::new("/resources")
        .join("sidecar")
        .join(format!("sptmod{}", std::env::consts::EXE_SUFFIX));
    assert_eq!(result, expected);
}

#[test]
fn binary_path_with_resource_dir_uses_bundled_sidecar_when_present_else_path_default() {
    let _guard = lock_env_guard();
    let _restore = EnvVarRestore::capture(driver::BINARY_ENV);
    // SAFETY: serialized by ENV_GUARD; no other code in this test binary
    // reads or writes SPTMOD_CLI concurrently.
    unsafe { std::env::remove_var(driver::BINARY_ENV) };

    let dir = tempfile::tempdir().unwrap();
    let sidecar_dir = dir.path().join("sidecar");
    std::fs::create_dir_all(&sidecar_dir).unwrap();
    let sidecar_file = sidecar_dir.join(format!("sptmod{}", std::env::consts::EXE_SUFFIX));
    std::fs::write(&sidecar_file, b"stub").unwrap();

    assert_eq!(
        driver::binary_path(Some(dir.path())),
        sidecar_file,
        "sidecar file exists on disk, must be preferred over the PATH default"
    );

    std::fs::remove_file(&sidecar_file).unwrap();

    assert_eq!(
        driver::binary_path(Some(dir.path())),
        PathBuf::from(driver::DEFAULT_BINARY),
        "sidecar file removed, must fall back to the PATH default"
    );
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

/// The committed `openapi.json` is the shared contract of the four transports
/// and already feeds the frontend's type generation, so deriving the expected
/// names from it keeps `FUNCTIONS` from drifting away from the Python service
/// functions without anyone having to edit a hand-copied list here.
#[test]
fn functions_matches_the_routes_of_the_committed_openapi_document() {
    let document: serde_json::Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../openapi.json"
    )))
    .expect("openapi.json parses as JSON");
    let expected: std::collections::HashSet<&str> = document["paths"]
        .as_object()
        .expect("openapi.json declares paths")
        .keys()
        .map(|path| {
            path.strip_prefix("/api/")
                .expect("every route is an /api/<name> path")
        })
        .collect();

    let actual: std::collections::HashSet<&str> = FUNCTIONS.iter().copied().collect();

    assert_eq!(
        actual.len(),
        FUNCTIONS.len(),
        "FUNCTIONS must contain no duplicates"
    );
    assert_eq!(actual, expected);
}

/// The `unknown_function` guard in `rpc::rpc` is `!FUNCTIONS.contains(&name)`
/// and needs an `AppHandle` to reach, so this exercises the same Tauri-free
/// seam: a name the contract does not list must not be accepted.
#[test]
fn functions_rejects_a_name_the_contract_does_not_list() {
    assert!(!FUNCTIONS.contains(&"totally_made_up_function"));
}

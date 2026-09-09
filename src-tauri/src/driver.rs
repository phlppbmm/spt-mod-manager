//! Subprocess driver for the Python `sptmod rpc` CLI.
//!
//! Stage 2 replaces the Python core with a native Rust core and deletes this
//! file together with the CLI it drives.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Once;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

use crate::rpc::RpcError;

pub const BINARY_ENV: &str = "SPTMOD_CLI";
pub const DEFAULT_BINARY: &str = "sptmod";
pub const SIDECAR_SUBDIR: &str = "sidecar";

const STDERR_TAIL_LINES: usize = 20;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProgressEvent {
    pub operation_id: String,
    pub step: String,
    pub percent: i64,
    pub message: String,
}

/// Where a bundled sidecar lives under Tauri's resource directory.
pub fn sidecar_path(resource_dir: &Path) -> PathBuf {
    resource_dir
        .join(SIDECAR_SUBDIR)
        .join(format!("{DEFAULT_BINARY}{}", std::env::consts::EXE_SUFFIX))
}

/// What to try after the `SPTMOD_CLI` override.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolutionOrder {
    /// Prefer an existing bundled sidecar over `sptmod` on `PATH`.
    SidecarFirst,
    /// Prefer `sptmod` on `PATH` over the bundled sidecar.
    PathFirst,
}

/// Resolution order in release builds ([`ResolutionOrder::SidecarFirst`]):
/// `SPTMOD_CLI`, then the bundled sidecar, then `sptmod` on `PATH`. In debug
/// builds ([`ResolutionOrder::PathFirst`]): `SPTMOD_CLI`, then `sptmod` on
/// `PATH`, so that a stale sidecar build in the dev resource directory cannot
/// shadow the developer's editable install. `PATH` itself is resolved by the OS
/// when the process is spawned, which is why the bare [`DEFAULT_BINARY`] name is
/// returned for it.
pub fn resolve_binary(
    env_value: Option<OsString>,
    bundled: Option<PathBuf>,
    order: ResolutionOrder,
    exists: &dyn Fn(&Path) -> bool,
) -> PathBuf {
    if let Some(value) = env_value {
        if !value.is_empty() {
            return PathBuf::from(value);
        }
    }
    match (order, bundled) {
        (ResolutionOrder::SidecarFirst, Some(path)) if exists(&path) => path,
        _ => PathBuf::from(DEFAULT_BINARY),
    }
}

/// The CLI to spawn, resolved against the process environment and the app's
/// resource directory, and logged once per process to stderr.
pub fn binary_path(resource_dir: Option<&Path>) -> PathBuf {
    let order = if cfg!(debug_assertions) {
        ResolutionOrder::PathFirst
    } else {
        ResolutionOrder::SidecarFirst
    };
    let binary = resolve_binary(
        std::env::var_os(BINARY_ENV),
        resource_dir.map(sidecar_path),
        order,
        &|path| path.exists(),
    );
    static LOGGED: Once = Once::new();
    LOGGED.call_once(|| eprintln!("sptmod: using CLI {} ({order:?})", binary.display()));
    binary
}

/// Run `<binary> rpc <name>` with `payload` on stdin, reporting every progress
/// line to `progress` and returning the final result or error line.
// Stage-2 option: serialise calls per game directory with a mutex. Stage 1
// relies on the Python core's own locking.
pub async fn call(
    binary: &Path,
    name: &str,
    payload: &Value,
    mut progress: impl FnMut(ProgressEvent),
) -> Result<Value, RpcError> {
    let mut command = Command::new(binary);
    command
        .arg("rpc")
        .arg(name)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().map_err(|error| {
        RpcError::new(
            format!("cannot run {}: {error}", binary.display()),
            "unreachable",
        )
    })?;

    if let Some(mut stdin) = child.stdin.take() {
        let request = payload.to_string();
        let _ = stdin.write_all(request.as_bytes()).await;
        let _ = stdin.shutdown().await;
    }

    let stderr = child.stderr.take();
    let diagnostics = tokio::spawn(async move {
        let mut buffer = String::new();
        if let Some(mut stderr) = stderr {
            let _ = stderr.read_to_string(&mut buffer).await;
        }
        buffer
    });

    let mut final_line: Option<Result<Value, RpcError>> = None;
    if let Some(stdout) = child.stdout.take() {
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            match parse_line(&line) {
                Some(Document::Progress(event)) => progress(event),
                Some(Document::Final(outcome)) => final_line = Some(outcome),
                None => continue,
            }
        }
    }

    let status = child.wait().await;
    let stderr = diagnostics.await.unwrap_or_default();

    if let Some(outcome) = final_line {
        return outcome;
    }
    let status = match status {
        Ok(status) => status.to_string(),
        Err(error) => format!("unknown exit status ({error})"),
    };
    Err(RpcError::new(
        format!(
            "{} rpc {name} produced no result ({status}){}",
            binary.display(),
            stderr_tail(&stderr)
        ),
        "bad_output",
    ))
}

enum Document {
    Progress(ProgressEvent),
    Final(Result<Value, RpcError>),
}

fn parse_line(line: &str) -> Option<Document> {
    let document: Value = serde_json::from_str(line).ok()?;
    match document.get("event")?.as_str()? {
        "progress" => serde_json::from_value(document)
            .ok()
            .map(Document::Progress),
        "result" => Some(Document::Final(Ok(document
            .get("data")
            .cloned()
            .unwrap_or(Value::Null)))),
        "error" => {
            let error: RpcError = serde_json::from_value(document.get("data")?.clone()).ok()?;
            Some(Document::Final(Err(error)))
        }
        _ => None,
    }
}

fn stderr_tail(stderr: &str) -> String {
    let lines: Vec<&str> = stderr.lines().collect();
    if lines.is_empty() {
        return String::new();
    }
    let tail = lines[lines.len().saturating_sub(STDERR_TAIL_LINES)..].join("\n");
    format!(":\n{tail}")
}

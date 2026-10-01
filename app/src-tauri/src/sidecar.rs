//! Spawns the Python core and bridges JSON-lines IPC (stdin/stdout) to the UI.
//!
//! The UI never talks to the network or the database: it only calls `core_request`, which
//! forwards one request line to the sidecar and waits for the response with the same id.
//! The sidecar's stdout carries only the protocol; its logs go to stderr.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Longest the UI waits for any single core action (connect can be slow on a bad network).
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);

type Pending = Arc<Mutex<HashMap<u64, Sender<Value>>>>;

pub struct Sidecar {
    child: Mutex<Child>,
    stdin: Mutex<ChildStdin>,
    pending: Pending,
    next_id: Mutex<u64>,
}

impl Sidecar {
    pub fn spawn() -> Result<Self, String> {
        let mut cmd = build_command();
        cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
        let mut child = cmd.spawn().map_err(|e| format!("could not start the HandOff core: {e}"))?;
        let stdin = child.stdin.take().ok_or("core stdin unavailable")?;
        let stdout = child.stdout.take().ok_or("core stdout unavailable")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));

        let reader_pending = Arc::clone(&pending);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let Ok(msg) = serde_json::from_str::<Value>(&line) else { continue };
                // A startup failure arrives as an error with a null id: fail every waiter with it.
                let Some(id) = msg.get("id").and_then(Value::as_u64) else {
                    if msg.get("error").is_some() {
                        for (_, tx) in reader_pending.lock().unwrap().drain() {
                            let _ = tx.send(msg.clone());
                        }
                    }
                    continue;
                };
                if let Some(tx) = reader_pending.lock().unwrap().remove(&id) {
                    let _ = tx.send(msg);
                }
            }
            // Core exited: unblock everyone with a clear error.
            let gone = json!({"error": {"code": "CORE_UNAVAILABLE",
                "message": "The HandOff core stopped unexpectedly."}});
            for (_, tx) in reader_pending.lock().unwrap().drain() {
                let _ = tx.send(gone.clone());
            }
        });

        Ok(Self { child: Mutex::new(child), stdin: Mutex::new(stdin), pending, next_id: Mutex::new(1) })
    }

    /// Sends one request and blocks for its response. Returns the `result` or the `error` object.
    pub fn request(&self, action: &str, payload: Value) -> Result<Value, Value> {
        let id = {
            let mut n = self.next_id.lock().unwrap();
            let id = *n;
            *n += 1;
            id
        };
        let (tx, rx) = channel();
        self.pending.lock().unwrap().insert(id, tx);
        let line = json!({"id": id, "action": action, "payload": payload}).to_string();
        {
            let mut stdin = self.stdin.lock().unwrap();
            if writeln!(stdin, "{line}").and_then(|_| stdin.flush()).is_err() {
                self.pending.lock().unwrap().remove(&id);
                return Err(unavailable());
            }
        }
        match rx.recv_timeout(REQUEST_TIMEOUT) {
            Ok(msg) => match (msg.get("result"), msg.get("error")) {
                (Some(r), _) => Ok(r.clone()),
                (_, Some(e)) => Err(e.clone()),
                _ => Err(unavailable()),
            },
            Err(_) => {
                self.pending.lock().unwrap().remove(&id);
                Err(json!({"code": "CORE_TIMEOUT", "message": "The HandOff core did not respond."}))
            }
        }
    }

    /// Closing stdin lets the core shut down cleanly; kill only if it does not.
    pub fn shutdown(&self) {
        drop(self.stdin.lock().map(|mut s| s.flush()));
        let mut child = self.child.lock().unwrap();
        for _ in 0..30 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
    }
}

fn unavailable() -> Value {
    json!({"code": "CORE_UNAVAILABLE", "message": "The HandOff core is not running."})
}

/// Dev: `uv run python -m handoff` from the repo's backend/. Two instances on one machine
/// use HANDOFF_DATA_DIR / HANDOFF_PORT. The packaged sidecar replaces this in M4.
fn build_command() -> Command {
    let backend = concat!(env!("CARGO_MANIFEST_DIR"), "/../../backend");
    let mut cmd = Command::new("uv");
    cmd.args(["run", "--project", backend, "python", "-m", "handoff"]);
    if let Ok(dir) = std::env::var("HANDOFF_DATA_DIR") {
        cmd.args(["--data-dir", &dir]);
    }
    if let Ok(port) = std::env::var("HANDOFF_PORT") {
        cmd.args(["--port", &port]);
    }
    if let Ok(bind) = std::env::var("HANDOFF_BIND") {
        cmd.args(["--bind", &bind]);
    }
    cmd
}

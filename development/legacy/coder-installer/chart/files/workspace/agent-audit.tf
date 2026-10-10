# Agent action monitoring: records what runs inside the workspace — every command/process an LLM agent (or anyone)
# executes, with its full argument list, working directory and parent process — and ships those records off the
# workspace to the coder-ui-updates service, which keeps them in Coder's CloudNativePG database for admins to review
# under Observability > Agent Activity.
#
# What this captures and why it fits an LLM agent: a coding agent acts by RUNNING THINGS — shell commands, git, curl,
# python, package installs, file tools. Process-exec audit records exactly those actions (auditd / snoopy class of
# control, standard in hardened Linux baselines). It is host/runtime audit of program executions, not keystroke
# capture of a person typing. Records leave the workspace immediately, so a process in the workspace cannot rewrite
# its own history after the fact.
#
# Attribution: every exec is stamped with pid/ppid and session id, so the subtree under a known agent process is
# identifiable as that agent's actions. The workspace owner and id come from Coder, and the record is tied to the
# workspace by its Coder agent token (validated by the service against Coder), so records cannot be forged for another
# workspace.
#
# Drop this file next to a template's main.tf. It needs a `coder_agent` named "main"; change agent_id if yours differs.
# Turn it off for a template by removing this file. AGENT_AUDIT_DISABLE=1 in the workspace disables it at runtime.

resource "coder_script" "agent_audit" {
  agent_id           = coder_agent.main.id
  display_name       = "Agent activity monitor"
  icon               = "/icon/lock.svg"
  run_on_start       = true
  start_blocks_login = false # auditing must not keep a user out if the store is briefly unreachable
  script             = <<-EOT
    #!/usr/bin/env bash
    # Best-effort: never fail a workspace start because auditing could not be set up.
    set -u
    if [ "$${AGENT_AUDIT_DISABLE:-0}" = "1" ]; then echo "Agent activity monitor disabled (AGENT_AUDIT_DISABLE=1)"; exit 0; fi

    ACCESS_URL='${data.coder_workspace.me.access_url}'
    OWNER='${data.coder_workspace_owner.me.name}'
    OWNER_ID='${data.coder_workspace_owner.me.id}'
    WS='${data.coder_workspace.me.name}'
    WS_ID='${data.coder_workspace.me.id}'
    LOGDIR="$HOME/.agent-audit"
    LOG="$LOGDIR/exec.log"
    SNOOPY_INI=/etc/snoopy.ini
    mkdir -p "$LOGDIR"; chmod 700 "$LOGDIR"

    # 1. Install the execve logger (snoopy: an ld.so.preload shim that records every execve through libc). Ubuntu
    #    universe packages it; snoopy-enable adds it to /etc/ld.so.preload so it covers processes started afterwards,
    #    the workspace's agents included.
    if [ ! -f /etc/ld.so.preload ] || ! grep -q snoopy /etc/ld.so.preload 2>/dev/null; then
      sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
      sudo DEBIAN_FRONTEND=noninteractive apt-get install -y snoopy >/dev/null 2>&1 || true
      sudo snoopy-enable >/dev/null 2>&1 || true
    fi

    # 2. Snoopy writes JSON lines to our file (not syslog: containers have no syslogd). One line per exec, with the
    #    fields that describe an action: when, who, where, the parent, the session, and the full command line.
    if command -v snoopy-enable >/dev/null 2>&1 && [ ! -f "$SNOOPY_INI.agent-audit" ]; then
      sudo tee "$SNOOPY_INI" >/dev/null <<'INI'
[snoopy]
error_logging = disabled
message = {"ts":"%{timestamp_utc}","uid":"%{uid}","user":"%{username}","cwd":"%{cwd}","tty":"%{tty}","sid":"%{sid}","pid":"%{pid}","ppid":"%{ppid}","login":"%{login}","cmd":%{cmdline_json}}
output = file:HERE_LOG
INI
      sudo sed -i "s#HERE_LOG#$LOG#" "$SNOOPY_INI"
      sudo touch "$LOG"; sudo chown "$(id -u):$(id -g)" "$LOG"; sudo chmod 640 "$LOG"
      sudo touch "$SNOOPY_INI.agent-audit"
    fi
    # snoopy older than 2.4 has no %{cmdline_json}: fall back to a quoted cmdline so the line still parses.
    if ! snoopy-test 2>/dev/null | grep -q cmdline_json; then
      sudo sed -i 's#,"cmd":%{cmdline_json}}#,"cmd":"%{cmdline}"}#' "$SNOOPY_INI" 2>/dev/null || true
    fi

    # 3. Forwarder: tail the log, batch new lines, and POST them to the service. Progress is remembered by byte
    #    offset so a restart never re-sends or drops. The workspace's Coder agent token authenticates the post; the
    #    service validates it against Coder and attaches the verified owner/workspace, so records can't be spoofed.
    cat > "$LOGDIR/forward.py" <<PY
    import json, os, time, urllib.request, urllib.error
    BASE = os.environ["ACCESS_URL"].rstrip("/") + "/__coder-ui/api/agent-audit"
    TOKEN = os.environ.get("CODER_AGENT_TOKEN", "")
    LOG = os.environ["LOG"]; OFF = LOG + ".offset"
    META = {k: os.environ[k] for k in ("OWNER", "OWNER_ID", "WS", "WS_ID")}
    def offset():
        try: return int(open(OFF).read().strip())
        except Exception: return 0
    def save(o):
        try: open(OFF, "w").write(str(o))
        except Exception: pass
    def post(events):
        body = json.dumps({"workspace": META, "events": events}).encode()
        req = urllib.request.Request(BASE, data=body, method="POST",
            headers={"Content-Type": "application/json", "Coder-Agent-Token": TOKEN, "X-Requested-With": "coder-ui"})
        with urllib.request.urlopen(req, timeout=15) as r: return r.status
    while True:
        try:
            size = os.path.getsize(LOG) if os.path.exists(LOG) else 0
            o = offset()
            if o > size: o = 0  # log was rotated/truncated
            if size > o:
                with open(LOG) as f:
                    f.seek(o)
                    lines = f.readlines()
                    new = f.tell()
                batch = []
                for ln in lines:
                    ln = ln.strip()
                    if not ln: continue
                    try: batch.append(json.loads(ln))
                    except Exception: batch.append({"raw": ln[:4000]})
                    if len(batch) >= 200:
                        post(batch); batch = []
                if batch: post(batch)
                save(new)
                # keep the local file bounded: once forwarded past 8 MB, trim to what is unsent
                if new > 8 * 1024 * 1024:
                    rest = open(LOG).read()[new:]
                    open(LOG, "w").write(rest); save(0)
        except urllib.error.URLError:
            pass  # store unreachable: keep the local log, retry next tick
        except Exception:
            pass
        time.sleep(2)
    PY
    sed -i 's/^    //' "$LOGDIR/forward.py"

    # One forwarder per workspace. Probe by a marker file's pid; snoopy would log a pgrep of our own name.
    if [ -f "$LOGDIR/forward.pid" ] && kill -0 "$(cat "$LOGDIR/forward.pid" 2>/dev/null)" 2>/dev/null; then
      echo "Agent activity monitor already running."
    else
      ACCESS_URL="$ACCESS_URL" OWNER="$OWNER" OWNER_ID="$OWNER_ID" WS="$WS" WS_ID="$WS_ID" LOG="$LOG" \
        nohup python3 "$LOGDIR/forward.py" >"$LOGDIR/forward.out" 2>&1 &
      echo $! > "$LOGDIR/forward.pid"
      echo "Agent activity monitor started; execs are recorded and forwarded to Observability > Agent Activity."
    fi
  EOT
}

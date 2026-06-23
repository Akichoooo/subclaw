#!/usr/bin/env bash
# run-claw-pool.sh - drive Claude Code workers through Claw Proxy.
#
# The default mode is proxy mode: workers talk to claw-proxy, the proxy owns the
# key pool, and each worker carries a stable x-session-id for key affinity.
# Default worker tools are read-only.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

KEYS_FILE="${CLAW_KEYS_FILE:-$HOME/.claude/scripts/claw-keys.tsv}"
FALLBACK_EP="${CLAW_FALLBACK_EP:-https://api.anthropic.com}"
FALLBACK_KEY="${CLAW_FALLBACK_KEY:-}"
MODEL="${CLAW_DEFAULT_MODEL:-}"
PROXY_URL="${CLAW_PROXY_URL:-http://localhost:4748}"
MODE="proxy"
JOBS=5
TIMEOUT=900
WORKDIR="$PWD"
OUTDIR=""
TASKDIR=""
TOOLS="Read,Glob,Grep"
PERMMODE="default"
OVERFLOW=0
STAMP="$(date +%Y%m%d_%H%M%S)"
ENABLE_TREE_UI="${CLAW_TREE_UI:-1}"
TREE_UI_PID=""
STATUS_JSON=""
TREE_UI_SCRIPT="${CLAW_TREE_UI_SCRIPT:-$SCRIPT_DIR/live_tree_ui.py}"
declare -a TASKS=()
PYTHON_BIN="${PYTHON:-}"
TIMEOUT_CMD=""
TIMEOUT_EXTRA_ARGS=()

usage() {
  cat <<'EOF'
run-claw-pool.sh - run subclaw worker tasks through Claude Code.

USAGE:
  run-claw-pool.sh [opts] <task1.md> [task2.md ...]
  run-claw-pool.sh [opts] -d <dir-of-task-md>

OPTS:
  -j N              max concurrent workers (default 5)
  -w DIR            working dir workers operate in (default: $PWD)
  -T SEC            per-task timeout seconds (default 900)
  -o DIR            report output dir (default <workdir>/.ai_agents/reports or ./claw-reports)
  -m MODEL          model id; if omitted in proxy mode, read /models default_model
  -d DIR            treat every *.md in DIR as a task file
  --direct          bypass proxy; use claw-keys.tsv keys directly
  --proxy-url URL   override proxy base (default http://localhost:4748)
  --overflow        direct mode: also use overflow keys
  --bash            add Bash to workers and bypass permissions
  --write           add Bash+Edit+Write tools and bypass permissions
  --tools T         comma list to override allowed tools
  --perm M          Claude permission mode
  --no-tree         do not start the optional tree UI process
  --info            print proxy model capacity and exit
EOF
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

resolve_timeout() {
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout"
  else
    echo "missing required command: timeout or gtimeout" >&2
    exit 1
  fi
  TIMEOUT_EXTRA_ARGS=()
  if "$TIMEOUT_CMD" --help 2>&1 | grep -q -- '--foreground'; then
    TIMEOUT_EXTRA_ARGS+=("--foreground")
  fi
  if "$TIMEOUT_CMD" --help 2>&1 | grep -q -- '--kill-after'; then
    TIMEOUT_EXTRA_ARGS+=("--kill-after=5s")
  fi
}

proxy_default_model() {
  curl -fsS "$PROXY_URL/models" 2>/dev/null |
    "$PYTHON_BIN" -c 'import json,sys; d=json.load(sys.stdin); data=d.get("data") or []; print(d.get("default_model") or (data[0].get("id") if data else ""))' 2>/dev/null
}

print_proxy_info() {
  curl -fsS "$PROXY_URL/models" |
    "$PYTHON_BIN" -c 'import json,sys
d=json.load(sys.stdin)
for m in d.get("data") or []:
    tiers=m.get("tiers") or ([m.get("tier")] if m.get("tier") else [])
    print("- Model: {} | Tiers: {} | Capacity (Keys): {} concurrent workers".format(
        m.get("id"), ",".join(tiers), m.get("key_count", m.get("capacity", 0))
    ))'
}

# Parse optional YAML-ish frontmatter (--- ... ---) at the top of a brief file.
# Emits two lines: "<tools_value>" and "<permission_value>" (empty if unset/invalid).
# Only known tool names are kept; unknown names are dropped with a warning to stderr.
# tools may be comma- OR space-separated; permission is a single token.
# tools/permission values from frontmatter OVERRIDE the global --tools/--perm/--bash/--write
# for this one task only. No frontmatter => empty => global defaults apply.
KNOWN_TOOLS="Read Glob Grep Bash Edit Write NotebookEdit"
VALID_PERM="default acceptEdits bypassPermissions"
# trim leading/trailing whitespace from $1
_trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }
parse_brief_frontmatter() {
  local f="$1"
  local in_fm=0 tools_out="" perm_out="" has_fm="no"
  # Only treat as frontmatter if it is the very first line of the file AND
  # there is a closing '---' (a matched pair). A lone leading '---' with no
  # closer is a decorative horizontal rule, NOT frontmatter — we must not
  # strip it or we'd send the worker an empty prompt.
  local first
  first="$(head -n1 "$f" 2>/dev/null)"
  [ "$first" = "---" ] || { echo ""; echo ""; echo "no"; return 0; }
  while IFS= read -r line; do
    [ "$line" = "---" ] && {
      in_fm=$((in_fm+1))
      if [ "$in_fm" -ge 2 ]; then has_fm="yes"; break; fi
      continue
    }
    case "$line" in
      tools:*)
        raw="$(_trim "${line#tools:}")"
        # accept both "Read,Glob,Grep" and "Read Glob Grep"
        tok_list="${raw//,/ }"
        cleaned=""
        for tok in $tok_list; do
          case " $KNOWN_TOOLS " in *" $tok "*) cleaned="${cleaned:+$cleaned,}$tok";; *) echo "frontmatter: ignoring unknown tool '$tok' in $f" >&2;; esac
        done
        tools_out="$cleaned"
        ;;
      permission:*)
        raw="$(_trim "${line#permission:}")"
        case " $VALID_PERM " in *" $raw "*) perm_out="$raw";; *) echo "frontmatter: ignoring invalid permission '$raw' in $f" >&2;; esac
        ;;
    esac
  done < "$f"
  echo "$tools_out"
  echo "$perm_out"
  echo "$has_fm"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -j) JOBS="$2"; shift 2;;
    -w) WORKDIR="$2"; shift 2;;
    -T) TIMEOUT="$2"; shift 2;;
    -o) OUTDIR="$2"; shift 2;;
    -m) MODEL="$2"; shift 2;;
    -d) TASKDIR="$2"; shift 2;;
    --direct) MODE="direct"; shift;;
    --proxy-url) PROXY_URL="$2"; shift 2;;
    --bash) TOOLS="$TOOLS,Bash"; PERMMODE="bypassPermissions"; shift;;
    --write) TOOLS="$TOOLS,Bash,Edit,Write,NotebookEdit"; PERMMODE="bypassPermissions"; shift;;
    --tools) TOOLS="$2"; shift 2;;
    --perm) PERMMODE="$2"; shift 2;;
    --overflow) OVERFLOW=1; shift;;
    --no-tree) ENABLE_TREE_UI=0; shift;;
    --info)
      need_cmd curl
      if [ -z "$PYTHON_BIN" ]; then
        if command -v python >/dev/null 2>&1; then PYTHON_BIN="python"
        elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"
        else echo "missing required command: python or python3" >&2; exit 1
        fi
      fi
      echo "=== Claw-Proxy Gateway Status ==="
      print_proxy_info
      exit 0
      ;;
    -h|--help) usage; exit 0;;
    -*) echo "unknown opt: $1" >&2; exit 2;;
    *) TASKS+=("$1"); shift;;
  esac
done

need_cmd claude
resolve_timeout
if [ -z "$PYTHON_BIN" ]; then
  if command -v python >/dev/null 2>&1; then PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"
  else echo "missing required command: python or python3" >&2; exit 1
  fi
fi

if [ -n "$TASKDIR" ]; then
  for f in "$TASKDIR"/*.md; do
    [ -f "$f" ] && TASKS+=("$f")
  done
fi
if [ "${#TASKS[@]}" -eq 0 ]; then
  echo "no task files given. use positional args or -d <dir>." >&2
  exit 2
fi

if [ "$MODE" = "proxy" ]; then
  need_cmd curl
  if [ -z "$MODEL" ]; then
    MODEL="$(proxy_default_model)"
    if [ -z "$MODEL" ]; then
      echo "cannot discover default model from $PROXY_URL/models; pass -m <model-id>" >&2
      exit 1
    fi
  fi
else
  MODEL="${MODEL:-claude-3-5-sonnet-20241022}"
fi

declare -a POOL_EP=() POOL_KEY=()
if [ "$MODE" = "direct" ]; then
  if [ -f "$KEYS_FILE" ]; then
    while IFS=$'\t' read -r tier ep key; do
      case "$tier" in ''|\#*) continue;; esac
      [ -z "$key" ] && continue
      if [ "$tier" = "primary" ] || { [ "$tier" = "overflow" ] && [ "$OVERFLOW" = 1 ]; }; then
        POOL_EP+=("$ep")
        POOL_KEY+=("$key")
      fi
    done < "$KEYS_FILE"
  fi
  if [ "${#POOL_KEY[@]}" -eq 0 ] && [ -n "$FALLBACK_KEY" ]; then
    POOL_EP+=("$FALLBACK_EP")
    POOL_KEY+=("$FALLBACK_KEY")
  fi
  if [ "${#POOL_KEY[@]}" -eq 0 ]; then
    echo "direct mode needs $KEYS_FILE or CLAW_FALLBACK_KEY" >&2
    exit 1
  fi
  NKEYS="${#POOL_KEY[@]}"
else
  NKEYS=1
fi

if [ -z "$OUTDIR" ]; then
  if [ -d "$WORKDIR/.ai_agents" ]; then
    OUTDIR="$WORKDIR/.ai_agents/reports"
  else
    OUTDIR="$PWD/claw-reports"
  fi
fi
mkdir -p "$OUTDIR"

STATUS_JSON="$OUTDIR/pool_status.$STAMP.json"

write_pool_status() {
  local msg="$1" running="$2" elapsed="${3:-0}"
  local wfiles=()
  local f
  for f in "$OUTDIR"/worker_*.status.json; do
    [ -f "$f" ] && wfiles+=("$f")
  done
  "$PYTHON_BIN" - "$STATUS_JSON" "$MODEL" "$msg" "$running" "$elapsed" "${wfiles[@]}" <<'PY'
import json
import sys

out, model, msg, running, elapsed, *files = sys.argv[1:]
workers = []
for path in files:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            workers.append(json.load(handle))
    except Exception:
        pass
payload = {
    "orchestrator": {
        "model": model,
        "msg": msg,
        "running": running == "true",
        "elapsed": int(float(elapsed or 0)),
    },
    "workers": workers,
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
}

cleanup_tree_ui() {
  [ -n "$TREE_UI_PID" ] && kill "$TREE_UI_PID" 2>/dev/null
}
trap cleanup_tree_ui EXIT INT TERM

rm -f "$OUTDIR"/worker_*.status.json "$OUTDIR"/worker_*.done.signal 2>/dev/null

if [ "$ENABLE_TREE_UI" = "1" ]; then
  write_pool_status "Initializing pool..." true 0
  if [ -f "$TREE_UI_SCRIPT" ]; then
    STATUS_JSON_UI="$(cygpath -w "$STATUS_JSON" 2>/dev/null || echo "$STATUS_JSON")"
    if command -v python >/dev/null 2>&1; then
      python "$TREE_UI_SCRIPT" "$STATUS_JSON_UI" 2 &
      TREE_UI_PID=$!
      sleep 0.3
    elif command -v python3 >/dev/null 2>&1; then
      python3 "$TREE_UI_SCRIPT" "$STATUS_JSON_UI" 2 &
      TREE_UI_PID=$!
      sleep 0.3
    else
      echo "tree UI disabled: python not found" >&2
      ENABLE_TREE_UI=0
    fi
  else
    echo "tree UI disabled: script not found at $TREE_UI_SCRIPT" >&2
    ENABLE_TREE_UI=0
  fi
fi

POOL_START_TIME="$(date +%s)"

echo "pool: ${#TASKS[@]} task(s), jobs=$JOBS, mode=$MODE, model=$MODEL"
if [ "$MODE" = "proxy" ]; then
  echo "proxy: $PROXY_URL (proxy owns keys; per-worker x-session-id pins one key)"
else
  echo "keys: $NKEYS (direct, overflow=$OVERFLOW)"
fi
echo "workdir: $WORKDIR  tools: $TOOLS  timeout=${TIMEOUT}s  out=$OUTDIR"
[ "$ENABLE_TREE_UI" = "1" ] && echo "tree UI: enabled (PID=$TREE_UI_PID, status=$STATUS_JSON)"

run_one() {
  local task="$1" idx="$2" ep="$3" key="$4"
  local base cfg report sid wstatus
  base="$(basename "$task" .md)"
  cfg="$(mktemp -d "${TMPDIR:-/tmp}/claw-w${idx}.XXXXXX")"
  report="$OUTDIR/${base}.claw.${STAMP}.md"
  wstatus="$OUTDIR/worker_$(printf '%03d' "$idx").status.json"
  sid="pool-${STAMP}-${base}-w${idx}"

  local t0 t1 dur ec keytag base_url auth_tok
  t0="$(date +%s)"

  write_worker_status() {
    local msg="$1" running="$2" status="$3" elapsed="$4"
    "$PYTHON_BIN" - "$wstatus" "$MODEL" "$msg" "$running" "$status" "$elapsed" <<'PY' 2>/dev/null
import json
import sys

out, model, msg, running, status, elapsed = sys.argv[1:]
payload = {
    "model": model,
    "msg": msg,
    "running": running == "true",
    "status": status,
    "elapsed": int(float(elapsed or 0)),
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
PY
    if [ "$ENABLE_TREE_UI" = "1" ]; then
      local orch_running=false
      [ "$(jobs -rp | wc -l)" -gt 0 ] && orch_running=true
      write_pool_status "Dispatching ${#TASKS[@]} tasks..." "$orch_running" "$(($(date +%s) - POOL_START_TIME))"
    fi
  }

  write_worker_status "$base" true "RUNNING" 0

  if [ "$MODE" = "proxy" ]; then
    base_url="$PROXY_URL"
    auth_tok="proxy-managed"
    keytag="proxy:${sid}"
  else
    base_url="$ep"
    auth_tok="$key"
    keytag="$(printf '%s' "$key" | cut -c1-6)..."
  fi

  local attempt=1
  local max_retries=3
  local monitor_pid=""
  ec=0

  start_progress_monitor() {
    (
      local last_progress=""
      local step_start_time
      step_start_time="$(date +%s)"
      tail -f -n0 "$report" 2>/dev/null | while IFS= read -r line; do
        case "$line" in
          *"[PROGRESS]"*)
            local prog now step_elapsed
            prog="${line#*\[PROGRESS\]}"
            prog="$(echo "$prog" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            if [ "$prog" != "$last_progress" ]; then
              now="$(date +%s)"
              step_elapsed=$((now - step_start_time))
              echo "[TIMESTAMP] $(date -Iseconds) | elapsed=${step_elapsed}s since last step" >> "$report"
              write_worker_status "$prog (${step_elapsed}s)" true "RUNNING" "$((now - t0))"
              last_progress="$prog"
              step_start_time="$now"
            fi
            ;;
          *"[WORKER_DONE]"*)
            echo "$(date +%s)" > "$OUTDIR/worker_$(printf '%03d' "$idx").done.signal"
            echo "[TIMESTAMP] $(date -Iseconds) | worker completed" >> "$report"
            ;;
        esac
      done
    ) &
    monitor_pid=$!
  }

  stop_progress_monitor() {
    [ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null
    if [ -n "$monitor_pid" ] && command -v pkill >/dev/null 2>&1; then
      pkill -P "$monitor_pid" 2>/dev/null
    fi
    monitor_pid=""
  }

  while [ "$attempt" -le "$max_retries" ]; do
    write_worker_status "$base (Attempt $attempt/$max_retries)" true "RUNNING" "$(($(date +%s) - t0))"
    {
      echo "[META]"
      echo "task: $task"
      echo "model: $MODEL"
      echo "workdir: $WORKDIR"
      echo "tools: $TOOLS"
      echo "mode: $MODE"
      echo "endpoint: $base_url"
      echo "session: $sid"
      echo "started: $(date -Iseconds)"
      echo "attempt: $attempt"
      echo "[/META]"
      echo
      echo "[OUTPUT]"
    } > "$report"

    start_progress_monitor
    local prompt
    prompt="$(cat "$task")"
    # Per-brief frontmatter override (--- tools: ... / permission: ... ---).
    # Falls back to the global $TOOLS / $PERMMODE when absent or empty.
    local task_tools="$TOOLS" task_perm="$PERMMODE" fm_has="no"
    local fm_tools fm_perm
    { read -r fm_tools; read -r fm_perm; read -r fm_has; } < <(parse_brief_frontmatter "$task")
    [ -n "$fm_tools" ] && task_tools="$fm_tools"
    [ -n "$fm_perm" ] && task_perm="$fm_perm"
    [ "$task_tools" != "$TOOLS" ] || [ "$task_perm" != "$PERMMODE" ] && \
      echo "[META] per-brief override: tools=$task_tools perm=$task_perm (global: tools=$TOOLS perm=$PERMMODE)" >> "$report"
    # Strip the frontmatter block from the prompt ONLY when a matched --- pair
    # was found. A lone leading '---' (decorative horizontal rule) or a
    # frontmatter missing its closer must NOT be stripped, or the worker would
    # receive an empty/garbled prompt.
    if [ "$fm_has" = "yes" ]; then
      prompt="$(awk 'BEGIN{f=0} /^---$/{f++; if(f>=2){f=-1; next} next} f>=1{next} f==-1{print}' "$task")"
    fi
    (
      cd "$WORKDIR" &&
        CLAUDE_CONFIG_DIR="$cfg" \
        ANTHROPIC_BASE_URL="$base_url" \
        ANTHROPIC_AUTH_TOKEN="$auth_tok" \
        ANTHROPIC_API_KEY= \
        ANTHROPIC_CUSTOM_HEADERS="x-session-id: $sid" \
        ANTHROPIC_MODEL="$MODEL" \
        ANTHROPIC_SMALL_FAST_MODEL="$MODEL" \
        DISABLE_TELEMETRY=1 \
        DISABLE_AUTOUPDATER=1 \
        DISABLE_NONESSENTIAL_TRAFFIC=1 \
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        "$TIMEOUT_CMD" "${TIMEOUT_EXTRA_ARGS[@]}" "$TIMEOUT" claude -p "$prompt" \
          --add-dir "$WORKDIR" \
          --allowedTools "$task_tools" \
          --permission-mode "$task_perm"
    ) >> "$report" 2>&1

    ec=$?
    stop_progress_monitor

    if [ "$ec" -eq 0 ]; then
      local self_status
      self_status="$(grep -A1 '\[WORKER_DONE\]' "$report" 2>/dev/null | grep -oE 'status:[[:space:]]*(OK|PARTIAL|FAIL)' | grep -oE '(OK|PARTIAL|FAIL)' | head -1)"
      [ "$self_status" = "PARTIAL" ] && break
    fi

    if [ "$ec" -eq 0 ] || [ "$ec" -eq 124 ]; then
      break
    fi

    attempt=$((attempt + 1))
    if [ "$attempt" -le "$max_retries" ]; then
      sleep $((2 ** attempt))
    fi
  done

  t1="$(date +%s)"
  dur=$((t1 - t0))
  {
    echo
    echo "[/OUTPUT]"
    echo "[EXIT] code=$ec duration_sec=$dur"
  } >> "$report"
  rm -rf "$cfg"
  rm -f "$OUTDIR/worker_$(printf '%03d' "$idx").done.signal" 2>/dev/null

  local status="OK"
  [ "$ec" -eq 124 ] && status="TIMEOUT"
  [ "$ec" -ne 0 ] && [ "$ec" -ne 124 ] && status="FAIL"

  local self_status
  self_status="$(grep -A1 '\[WORKER_DONE\]' "$report" 2>/dev/null | grep -oE 'status:[[:space:]]*(OK|PARTIAL|FAIL)' | grep -oE '(OK|PARTIAL|FAIL)' | head -1)"
  if [ -n "$self_status" ] && [ "$ec" -eq 0 ]; then
    [ "$self_status" = "PARTIAL" ] && status="PARTIAL"
    [ "$self_status" = "FAIL" ] && status="FAIL"
  fi

  local final_msg="Done"
  [ "$status" = "TIMEOUT" ] && final_msg="Timed out"
  [ "$status" = "FAIL" ] && final_msg="Escalated to orchestrator"
  [ "$status" = "PARTIAL" ] && final_msg="Partial"
  write_worker_status "$final_msg ($base)" false "$status" "$dur"
  echo "WORKER idx=$idx key=$keytag status=$status ec=$ec dur=${dur}s report=$report"
}

i=0
for task in "${TASKS[@]}"; do
  i=$((i + 1))
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do
    wait -n
  done
  if [ "$MODE" = "direct" ]; then
    ki=$(((i - 1) % NKEYS))
    run_one "$task" "$i" "${POOL_EP[$ki]}" "${POOL_KEY[$ki]}" &
  else
    run_one "$task" "$i" "$PROXY_URL" "proxy-managed" &
  fi
done
wait

ok=0
fail=0
to=0
for task in "${TASKS[@]}"; do
  base="$(basename "$task" .md)"
  r="$OUTDIR/${base}.claw.${STAMP}.md"
  if grep -q '\[EXIT\] code=0 ' "$r" 2>/dev/null; then
    ok=$((ok + 1))
  elif grep -q '\[EXIT\] code=124 ' "$r" 2>/dev/null; then
    to=$((to + 1))
  else
    fail=$((fail + 1))
  fi
done

if [ "$ENABLE_TREE_UI" = "1" ]; then
  total_elapsed=$(($(date +%s) - POOL_START_TIME))
  write_pool_status "Pool complete: OK=$ok FAIL=$fail TIMEOUT=$to" false "$total_elapsed"
  sleep 1.5
fi

echo "POOL_DONE OK=$ok FAIL=$fail TIMEOUT=$to OUTDIR=$OUTDIR STAMP=$STAMP"

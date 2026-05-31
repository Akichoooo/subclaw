#!/usr/bin/env bash
# run-claw-pool.sh — drive worker models through the Claude Code CLI, pooled.
#
# WHY: many cheap-model endpoints are Anthropic-protocol compatible, so the most
# mature driver is `claude` itself. Running workers through Claude Code (native
# UTF-8 JSON) avoids the GBK mojibake / lone-surrogate "Failed to parse JSON"
# crashes a raw OpenAI runner suffers.
#
# Each worker gets an isolated CLAUDE_CONFIG_DIR so it bypasses the user's
# personal proxy settings in ~/.claude/settings.json.
#
# KEY ROUTING — two modes:
#   PROXY (default): workers talk to the claw-proxy at localhost:4748. The proxy
#     owns the key pool, pins one key per worker session (x-session-id we inject)
#     for prompt-cache locality, and transparently fails over to another key only
#     on a real 429. Workers carry NO real key. True multi-key parallel without
#     the single-key multi-open rate limit.
#   DIRECT (--direct): legacy path. Workers round-robin keys from claw-keys.tsv
#     themselves and hit the upstream /anthropic endpoint directly (no proxy).
#
# USAGE:
#   run-claw-pool.sh [opts] <task1.md> [task2.md ...]
#   run-claw-pool.sh [opts] -d <dir-of-task-md>
#
# OPTS:
#   -j N      max concurrent workers (default 5)
#   -w DIR    working dir workers operate in (default: $PWD)
#   -T SEC    per-task timeout seconds (default 900)
#   -o DIR    report output dir (default <workdir>/.ai_agents/reports or ./claw-reports)
#   -m MODEL  model id (from claw-proxy /models; default from CLAW_DEFAULT_MODEL)
#   -d DIR    treat every *.md in DIR as a task file
#   --direct    bypass the proxy; workers use claw-keys.tsv keys directly (legacy)
#   --proxy-url URL  override proxy base (default http://localhost:4748)
#   --overflow  (direct mode) also use pay-as-you-go overflow keys, not just primary
#   --bash    add Bash to workers (implies --permission-mode bypassPermissions)
#   --write   add Bash+Edit+Write to workers (implies bypassPermissions)
#   --tools T comma list to override allowed tools entirely
#   --perm M  set permission-mode (default|acceptEdits|bypassPermissions|plan)
#
# SAFETY: default is read-only (Read,Glob,Grep, permission-mode=default) so a
#   worker can scan/audit but cannot run shell or mutate files. --bash/--write
#   grant autonomous shell/edit power to every worker — use only on trusted tasks.
#
# KEYS (direct mode only): ~/.claude/scripts/claw-keys.tsv (tier<TAB>endpoint<TAB>key).
#   primary = unlimited/cheap (default); overflow = pay-as-you-go (--overflow).
#   In proxy mode the proxy's keys.json is authoritative; this file is unused.
#
# Each task -> one report file. Final line: POOL_DONE OK=<n> FAIL=<n> ...
set -uo pipefail

KEYS_FILE="${CLAW_KEYS_FILE:-$HOME/.claude/scripts/claw-keys.tsv}"
AUTH_JSON="$HOME/.local/share/opencode/auth.json"
FALLBACK_EP="${CLAW_FALLBACK_EP:-https://api.anthropic.com/v1/messages}"
MODEL="${CLAW_DEFAULT_MODEL:-claude-3-5-sonnet-20241022}"
PROXY_URL="${CLAW_PROXY_URL:-http://localhost:4748}"
MODE="proxy"   # proxy (default) | direct
JOBS=5; TIMEOUT=900; WORKDIR="$PWD"; OUTDIR=""; TASKDIR=""
# Safe default: read-only tools, no Bash, no permission bypass. Workers cannot
# run arbitrary shell or mutate files unless the caller explicitly opts in.
TOOLS="Read,Glob,Grep"; PERMMODE="default"; OVERFLOW=0
STAMP="$(date +%Y%m%d_%H%M%S)"
declare -a TASKS=()
# Live tree UI control
ENABLE_TREE_UI="${CLAW_TREE_UI:-1}"  # set CLAW_TREE_UI=0 to disable
TREE_UI_PID=""
STATUS_JSON=""

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
    --bash)  TOOLS="$TOOLS,Bash"; PERMMODE="bypassPermissions"; shift;;
    --write) TOOLS="$TOOLS,Bash,Edit,Write,NotebookEdit"; PERMMODE="bypassPermissions"; shift;;
    --tools) TOOLS="$2"; shift 2;;
    --perm)  PERMMODE="$2"; shift 2;;
    --overflow) OVERFLOW=1; shift;;
    --no-tree) ENABLE_TREE_UI=0; shift;;
    --info)
      echo "=== Claw-Proxy Gateway Status ==="
      curl -s "$PROXY_URL/models" | jq -r '.data[] | "- Model: \(.id) | Tiers: \(.tiers|join(",")) | Capacity (Keys): \(.key_count) concurrent workers"' 2>/dev/null || echo "Proxy unreachable at $PROXY_URL"
      exit 0
      ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) echo "unknown opt: $1" >&2; exit 2;;
    *) TASKS+=("$1"); shift;;
  esac
done

# collect tasks from -d dir
if [ -n "$TASKDIR" ]; then
  for f in "$TASKDIR"/*.md; do [ -f "$f" ] && TASKS+=("$f"); done
fi
if [ "${#TASKS[@]}" -eq 0 ]; then
  echo "no task files given. use positional args or -d <dir>." >&2; exit 2
fi

# load (endpoint,key) pool — DIRECT mode only. proxy mode lets the proxy own keys.
declare -a POOL_EP=() POOL_KEY=()
if [ "$MODE" = "direct" ]; then
  if [ -f "$KEYS_FILE" ]; then
    while IFS=$'\t' read -r tier ep key; do
      case "$tier" in ''|\#*) continue;; esac
      [ -z "$key" ] && continue
      if [ "$tier" = "primary" ] || { [ "$tier" = "overflow" ] && [ "$OVERFLOW" = 1 ]; }; then
        POOL_EP+=("$ep"); POOL_KEY+=("$key")
      fi
    done < "$KEYS_FILE"
  fi
  # fallback: single tp- key from auth.json if pool file missing/empty
  if [ "${#POOL_KEY[@]}" -eq 0 ]; then
    k="$(grep -oE 'tp-[A-Za-z0-9]+' "$AUTH_JSON" 2>/dev/null | head -1)"
    if [ -z "$k" ]; then echo "no keys: populate $KEYS_FILE or add a tp- key to $AUTH_JSON" >&2; exit 1; fi
    POOL_EP+=("$FALLBACK_EP"); POOL_KEY+=("$k")
  fi
  NKEYS="${#POOL_KEY[@]}"
else
  # proxy mode: no local keys; the proxy at $PROXY_URL routes & pins keys itself.
  # workers carry a dummy token (proxy ignores it) + a stable x-session-id.
  NKEYS=1
fi

# resolve output dir
if [ -z "$OUTDIR" ]; then
  if [ -d "$WORKDIR/.ai_agents" ]; then OUTDIR="$WORKDIR/.ai_agents/reports"
  else OUTDIR="$PWD/claw-reports"; fi
fi
mkdir -p "$OUTDIR"

# setup live tree UI
STATUS_JSON="$OUTDIR/pool_status.$STAMP.json"
update_status() {
  # $1=orchestrator_msg, $2=orchestrator_running, $3=orchestrator_elapsed
  local orch_msg="$1" orch_run="$2" orch_elapsed="${3:-0}"
  local workers_json="[]"
  # collect worker states from temp files
  local wfiles=("$OUTDIR"/worker_*.status.json)
  if [ -e "${wfiles[0]}" ]; then
    workers_json="$(jq -s '.' "${wfiles[@]}" 2>/dev/null || echo '[]')"
  fi
  jq -n --arg msg "$orch_msg" --argjson run "$orch_run" --argjson elapsed "$orch_elapsed" \
        --arg model "$MODEL" --argjson workers "$workers_json" \
    '{orchestrator: {model: $model, msg: $msg, running: $run, elapsed: $elapsed}, workers: $workers}' \
    > "$STATUS_JSON"
}

cleanup_tree_ui() {
  [ -n "$TREE_UI_PID" ] && kill "$TREE_UI_PID" 2>/dev/null
  rm -f "$OUTDIR"/worker_*.status.json "$STATUS_JSON" 2>/dev/null
}
trap cleanup_tree_ui EXIT INT TERM

# 清理旧的状态文件（避免聚合上次运行的残留 worker 状态）
rm -f "$OUTDIR"/worker_*.status.json "$OUTDIR"/worker_*.done.signal 2>/dev/null

if [ "$ENABLE_TREE_UI" = "1" ]; then
  update_status "Initializing pool..." true 0
  # Convert git-bash path to Windows path for Python
  STATUS_JSON_WIN="$(cygpath -w "$STATUS_JSON" 2>/dev/null || echo "$STATUS_JSON")"
  python "$HOME/.claude/scripts/live_tree_ui.py" "$STATUS_JSON_WIN" 2 &
  TREE_UI_PID=$!
  sleep 0.3  # let UI start
fi

POOL_START_TIME="$(date +%s)"

echo "pool: ${#TASKS[@]} task(s), jobs=$JOBS, mode=$MODE, model=$MODEL"
if [ "$MODE" = "proxy" ]; then echo "proxy: $PROXY_URL (proxy owns keys; per-worker x-session-id pins one key)"
else echo "keys: $NKEYS (direct, overflow=$OVERFLOW)"; fi
echo "workdir: $WORKDIR  tools: $TOOLS  timeout=${TIMEOUT}s  out=$OUTDIR"
[ "$ENABLE_TREE_UI" = "1" ] && echo "tree UI: enabled (PID=$TREE_UI_PID, status=$STATUS_JSON)"

run_one() {
  local task="$1" idx="$2" ep="$3" key="$4"
  local base; base="$(basename "$task" .md)"
  local cfg report sid wstatus
  cfg="$(mktemp -d "${TMPDIR:-/tmp}/claw-w${idx}.XXXXXX")"
  report="$OUTDIR/${base}.claw.${STAMP}.md"
  wstatus="$OUTDIR/worker_$(printf '%03d' "$idx").status.json"
  # stable per-worker session id -> proxy pins one key for this worker (cache locality)
  sid="pool-${STAMP}-${base}-w${idx}"
  local t0 t1 dur ec keytag base_url auth_tok
  t0="$(date +%s)"
  # mark worker RUNNING in the live tree
  write_worker_status() {
    # $1=msg $2=running(bool) $3=status $4=elapsed
    jq -n --arg model "$MODEL" --arg msg "$1" --argjson run "$2" \
          --arg status "$3" --argjson elapsed "$4" \
      '{model: $model, msg: $msg, running: $run, status: $status, elapsed: $elapsed}' \
      > "$wstatus" 2>/dev/null
    # trigger aggregation: rebuild pool_status.json from all worker files
    if [ "$ENABLE_TREE_UI" = "1" ]; then
      local workers_json="[]"
      local wfiles=("$OUTDIR"/worker_*.status.json)
      if [ -e "${wfiles[0]}" ]; then
        workers_json="$(jq -s '.' "${wfiles[@]}" 2>/dev/null || echo '[]')"
      fi
      # orchestrator stays "running" while any worker is active
      local orch_running=false
      if [ "$(jobs -rp | wc -l)" -gt 0 ]; then orch_running=true; fi
      local orch_elapsed=$(($(date +%s) - POOL_START_TIME))
      jq -n --arg model "$MODEL" --arg msg "Dispatching ${#TASKS[@]} tasks..." \
            --argjson run "$orch_running" --argjson elapsed "$orch_elapsed" \
            --argjson workers "$workers_json" \
        '{orchestrator: {model: $model, msg: $msg, running: $run, elapsed: $elapsed}, workers: $workers}' \
        > "$STATUS_JSON" 2>/dev/null
    fi
  }
  write_worker_status "$base" true "RUNNING" 0
  if [ "$MODE" = "proxy" ]; then
    base_url="$PROXY_URL"; auth_tok="proxy-managed"; keytag="proxy:${sid}"
  else
    base_url="$ep"; auth_tok="$key"; keytag="$(printf '%s' "$key" | cut -c1-6)…"
  fi
  local attempt=1
  local max_retries=3
  local ec=0

  # 进度监控器：后台 tail -f 报告文件，解析 [PROGRESS]/[WORKER_DONE] 标记，
  # 让 worker 能主动报告进度，主 Claude 实时感知（不必等超时）
  local monitor_pid=""
  start_progress_monitor() {
    (
      local last_progress=""
      local step_start_time="$(date +%s)"
      # tail -f 跟随文件增长，解析标记
      tail -f -n0 "$report" 2>/dev/null | while IFS= read -r line; do
        case "$line" in
          *"[PROGRESS]"*)
            # 提取 [PROGRESS] 后的内容作为实时状态
            local prog="${line#*\[PROGRESS\]}"
            prog="$(echo "$prog" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            if [ "$prog" != "$last_progress" ]; then
              local now="$(date +%s)"
              local step_elapsed=$((now - step_start_time))
              # 在报告中添加时间戳（追加到原始行后）
              echo "[TIMESTAMP] $(date -Iseconds) | elapsed=${step_elapsed}s since last step" >> "$report"
              # 更新 worker status（带时间戳）
              write_worker_status "$prog (${step_elapsed}s)" true "RUNNING" $(( now - t0 ))
              last_progress="$prog"
              step_start_time="$now"
            fi
            ;;
          *"[WORKER_DONE]"*)
            # worker 主动声明完成 → 立即记录信号文件
            echo "$(date +%s)" > "$OUTDIR/worker_$(printf '%03d' "$idx").done.signal"
            # 在报告中添加完成时间戳
            echo "[TIMESTAMP] $(date -Iseconds) | worker completed" >> "$report"
            ;;
        esac
      done
    ) &
    monitor_pid=$!
  }
  stop_progress_monitor() {
    [ -n "$monitor_pid" ] && kill "$monitor_pid" 2>/dev/null
    # 杀掉 tail -f 子进程
    pkill -P "$monitor_pid" 2>/dev/null
    monitor_pid=""
  }

  while [ "$attempt" -le "$max_retries" ]; do
    write_worker_status "$base (Attempt $attempt/$max_retries)" true "RUNNING" $(( $(date +%s) - t0 ))
    {
      echo "[META]"; echo "task: $task"; echo "model: $MODEL"
      echo "workdir: $WORKDIR"; echo "tools: $TOOLS"; echo "mode: $MODE"
      echo "endpoint: $base_url"; echo "session: $sid"; echo "started: $(date -Iseconds)"
      echo "attempt: $attempt"; echo "[/META]"; echo; echo "[OUTPUT]"
    } > "$report"

    # 启动进度监控（报告文件已创建，可以 tail）
    start_progress_monitor

    local prompt; prompt="$(cat "$task")"
    ( cd "$WORKDIR" && \
      CLAUDE_CONFIG_DIR="$cfg" \
      ANTHROPIC_BASE_URL="$base_url" \
      ANTHROPIC_AUTH_TOKEN="$auth_tok" \
      ANTHROPIC_API_KEY= \
      ANTHROPIC_CUSTOM_HEADERS="x-session-id: $sid" \
      ANTHROPIC_MODEL="$MODEL" \
      ANTHROPIC_SMALL_FAST_MODEL="$MODEL" \
      DISABLE_TELEMETRY=1 DISABLE_AUTOUPDATER=1 DISABLE_NONESSENTIAL_TRAFFIC=1 \
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        timeout --foreground --kill-after=5s "$TIMEOUT" claude -p "$prompt" \
          --add-dir "$WORKDIR" \
          --allowedTools "$TOOLS" \
          --permission-mode "$PERMMODE" \
    ) >> "$report" 2>&1

    ec=$?
    stop_progress_monitor

    # 检查 worker 是否声明了 PARTIAL 状态（有部分成果，不应重试）
    if [ "$ec" -eq 0 ]; then
      local self_status
      self_status="$(grep -A1 '\[WORKER_DONE\]' "$report" 2>/dev/null | grep -oE 'status:[[:space:]]*(OK|PARTIAL|FAIL)' | grep -oE '(OK|PARTIAL|FAIL)' | head -1)"
      if [ "$self_status" = "PARTIAL" ]; then
        # PARTIAL 状态：有部分成果，不重试（重试会丢失已完成的工作）
        break
      fi
    fi

    if [ "$ec" -eq 0 ] || [ "$ec" -eq 124 ]; then
      break # Success or Timeout, exit retry loop
    fi

    attempt=$((attempt + 1))
    if [ "$attempt" -le "$max_retries" ]; then
      # 指数退避：2s → 4s → 8s（与 claw-proxy 的重试策略一致）
      local backoff=$((2 ** attempt))
      sleep "$backoff"
    fi
  done

  t1="$(date +%s)"; dur=$((t1 - t0))
  { echo; echo "[/OUTPUT]"; echo "[EXIT] code=$ec duration_sec=$dur"; } >> "$report"
  rm -rf "$cfg"
  rm -f "$OUTDIR/worker_$(printf '%03d' "$idx").done.signal" 2>/dev/null
  local status="OK"
  [ "$ec" -eq 124 ] && status="TIMEOUT"
  [ "$ec" -ne 0 ] && [ "$ec" -ne 124 ] && status="FAIL"
  # 如果 worker 主动声明了 [WORKER_DONE]，提取其 status
  local self_status
  self_status="$(grep -A1 '\[WORKER_DONE\]' "$report" 2>/dev/null | grep -oE 'status:[[:space:]]*(OK|PARTIAL|FAIL)' | grep -oE '(OK|PARTIAL|FAIL)' | head -1)"
  if [ -n "$self_status" ] && [ "$ec" -eq 0 ]; then
    [ "$self_status" = "PARTIAL" ] && status="PARTIAL"
    [ "$self_status" = "FAIL" ] && status="FAIL"
  fi
  # final worker state for the tree
  local final_msg="Done"
  [ "$status" = "TIMEOUT" ] && final_msg="Timed out"
  [ "$status" = "FAIL" ] && final_msg="Escalated to Orchestrator (FAIL)"
  [ "$status" = "PARTIAL" ] && final_msg="Partial (needs follow-up)"
  write_worker_status "$final_msg ($base)" false "$status" "$dur"
  echo "WORKER idx=$idx key=$keytag status=$status ec=$ec dur=${dur}s report=$report"
}

# pooled execution: cap concurrency with wait -n.
# proxy mode: proxy load-balances by x-session-id, so ep/key args are placeholders.
# direct mode: round-robin the local key pool across workers.
i=0
for task in "${TASKS[@]}"; do
  i=$((i+1))
  while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
  if [ "$MODE" = "direct" ]; then
    ki=$(( (i-1) % NKEYS ))
    run_one "$task" "$i" "${POOL_EP[$ki]}" "${POOL_KEY[$ki]}" &
  else
    run_one "$task" "$i" "$PROXY_URL" "proxy-managed" &
  fi
done
wait

# tally from reports
ok=0; fail=0; to=0
for task in "${TASKS[@]}"; do
  base="$(basename "$task" .md)"
  r="$OUTDIR/${base}.claw.${STAMP}.md"
  if grep -q '\[EXIT\] code=0 ' "$r" 2>/dev/null; then ok=$((ok+1))
  elif grep -q '\[EXIT\] code=124 ' "$r" 2>/dev/null; then to=$((to+1))
  else fail=$((fail+1)); fi
done

# final orchestrator state: pool complete -> tree shows green check
if [ "$ENABLE_TREE_UI" = "1" ]; then
  total_elapsed=$(($(date +%s) - POOL_START_TIME))
  workers_json="[]"
  wfiles=("$OUTDIR"/worker_*.status.json)
  if [ -e "${wfiles[0]}" ]; then
    workers_json="$(jq -s '.' "${wfiles[@]}" 2>/dev/null || echo '[]')"
  fi
  jq -n --arg model "$MODEL" \
        --arg msg "Pool complete: OK=$ok FAIL=$fail TIMEOUT=$to" \
        --argjson run false --argjson elapsed "$total_elapsed" \
        --argjson workers "$workers_json" \
    '{orchestrator: {model: $model, msg: $msg, running: $run, elapsed: $elapsed}, workers: $workers}' \
    > "$STATUS_JSON" 2>/dev/null
  sleep 1.5  # let final frame render before cleanup trap fires
fi

echo "POOL_DONE OK=$ok FAIL=$fail TIMEOUT=$to OUTDIR=$OUTDIR STAMP=$STAMP"

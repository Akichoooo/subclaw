"""
Claw-Proxy: Multi-Model Agent Orchestration Gateway
Author: @Akichoooo

Session-affinity key rotation + protocol translation (Anthropic/OpenAI).
Supports dynamic model routing, tier-based failover, and tool-call passthrough.
每次请求轮询下一�?key，均匀消耗�?带缓存命中统计、empty-text-block sanitize、degraded 重试�?"""

import os
import sys
import json
import hashlib
import time
import asyncio
import logging
import re
import threading
from typing import Optional, Dict, Any, List, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
import httpx
import uvicorn

LOG_FORMAT = "%(asctime)s [claw-proxy] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
logger = logging.getLogger("claw-proxy")

PROXY_PORT = 4748

REQUEST_TIMEOUT_SEC = 900  # match run-claw-pool.sh -T default (15min for long subclaw tasks)
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5

# Structured timeout: connect fast, read patient, detect dead sockets
_HTTPX_TIMEOUT = httpx.Timeout(
    connect=15.0,       # TUN VPN can be slow to establish
    read=900.0,         # match run-claw-pool.sh default -T 900 (15min for long tasks)
    write=30.0,         # sending the request body
    pool=15.0,          # waiting for a connection from the pool
)
# Idle stream timeout: if no data arrives for this many seconds, treat as dead
_STREAM_IDLE_TIMEOUT_SEC = 90
CLAUDE_TOKEN_MULTIPLIER = 1.15

# ============================================
# Load key pool from keys.json (generic multi-provider)
# ============================================
_keys_file = os.path.join(os.path.dirname(__file__), "keys.json")
with open(_keys_file, "r", encoding="utf-8") as f:
    CLAW_KEYS = json.load(f)["keys"]
# ============================================

# ============================================
# Session-aware key affinity + model routing
# 核心调度�?(Core Scheduler):
# 1. session id (x-session-id header) 决定了缓存的亲和�?(Cache Affinity).
# 2. 将同一�?session 强绑定到同一�?API Key，最大化利用大模型的 Prompt Cache，极大节�?Token 费用�?# 3. �?session �?在匹配该 model �?primary key 里选活跃最少的，实现并发负载均衡�?# 4. 如果触发 429 限流，自动漂移到下一个可�?Key (rebind_session)�?# ============================================
_session_map: Dict[str, int] = {}
_key_sessions: Dict[int, set] = {}
_session_ttl: Dict[str, float] = {}
_session_lock = threading.Lock()
_SESSION_TIMEOUT = 600  # 10 分钟无活动自动释�?_next_key_idx = 0


def _entry_matches_model(entry: Dict, model: str) -> bool:
    """entry �?models 字段 = 通配; 否则要求 model 命中�?""
    models = entry.get("models")
    if not models:
        return True
    return model in models


def routing_for_model(model: str) -> List[int]:
    """返回能服务该 model �?key 下标, primary 在前 overflow 在后 (失败时按此顺序升�?�?""
    matching = [i for i, e in enumerate(CLAW_KEYS) if _entry_matches_model(e, model)]
    if not matching:
        matching = list(range(len(CLAW_KEYS)))
    matching.sort(key=lambda i: 0 if CLAW_KEYS[i].get("tier", "primary") == "primary" else 1)
    return matching


def _release_stale(now: float):
    stale = [s for s, t in list(_session_ttl.items()) if now - t > _SESSION_TIMEOUT]
    for s in stale:
        old_key = _session_map.pop(s, None)
        if old_key is not None and old_key in _key_sessions:
            _key_sessions[old_key].discard(s)
        _session_ttl.pop(s, None)


def pick_key_for_session(session_id: str, model: str) -> int:
    """同一 session_id 固定同一 key; �?session 在匹�?model �?primary key 里选负载最轻的。返�?key 下标�?""
    global _next_key_idx
    now = time.time()
    with _session_lock:
        _release_stale(now)

        # 已有 session 且该 key 仍能服务�?model �?复用 (保缓�?
        if session_id in _session_map:
            key_idx = _session_map[session_id]
            if _entry_matches_model(CLAW_KEYS[key_idx], model):
                _session_ttl[session_id] = now
                return key_idx

        matching = routing_for_model(model)
        primary = [i for i in matching if CLAW_KEYS[i].get("tier", "primary") == "primary"] or matching
        active_counts = {i: len(_key_sessions.get(i, set())) for i in primary}
        min_count = min(active_counts.values())
        candidates = [i for i, c in active_counts.items() if c == min_count]
        key_idx = candidates[_next_key_idx % len(candidates)]
        _next_key_idx = (_next_key_idx + 1) % len(CLAW_KEYS)

        _session_map[session_id] = key_idx
        _key_sessions.setdefault(key_idx, set()).add(session_id)
        _session_ttl[session_id] = now

        logger.info(
            f"SESSION ASSIGN | {session_id} -> key[{key_idx}] "
            f"...{CLAW_KEYS[key_idx]['key'][-6:]} ({CLAW_KEYS[key_idx].get('tier','primary')}) | "
            f"active_sessions={{{', '.join(f'{k}:{len(v)}' for k, v in sorted(_key_sessions.items()))}}}"
        )
        return key_idx


def rebind_session(session_id: str, new_idx: int):
    """429 失败转移: �?session 重新钉到�?key �?(后续请求保持在新 key 以维持缓�?�?""
    with _session_lock:
        old = _session_map.get(session_id)
        if old is not None and old != new_idx and old in _key_sessions:
            _key_sessions[old].discard(session_id)
        _session_map[session_id] = new_idx
        _key_sessions.setdefault(new_idx, set()).add(session_id)
        _session_ttl[session_id] = time.time()
        logger.warning(f"SESSION REBIND | {session_id} -> key[{new_idx}] ...{CLAW_KEYS[new_idx]['key'][-6:]} (429 failover)")


def session_count_for_key(key_idx: int) -> int:
    with _session_lock:
        return len(_key_sessions.get(key_idx, set()))


_DEGRADATION_PATTERNS = re.compile(
    r"(temporarily unavailable|safety classifier|classifier error|"
    r"overloaded|rate.?limit|try again|server error|internal error|"
    r"upstream error|bad gateway)",
    re.IGNORECASE,
)


# --- ConversationCache (缓存命中统计) ---

class ConversationCache:
    def __init__(self):
        self._system_prompts: Dict[str, str] = {}
        self._tool_specs: Dict[str, str] = {}
        self._message_logs: Dict[str, List[Dict]] = {}

    @staticmethod
    def _fingerprint(system_prompt: str, tools_json: str) -> str:
        h = hashlib.sha256()
        h.update(system_prompt.encode("utf-8"))
        h.update(tools_json.encode("utf-8"))
        return h.hexdigest()[:16]

    @staticmethod
    def _sorted_tools_json(tools: Optional[List[Dict]]) -> str:
        if not tools:
            return "[]"
        sorted_tools = sorted(tools, key=lambda t: t.get("name", ""))
        return json.dumps(sorted_tools, sort_keys=True, ensure_ascii=False)

    def resolve_fingerprint(self, system_prompt: str, tools: Optional[List[Dict]]) -> str:
        return self._fingerprint(system_prompt, self._sorted_tools_json(tools))

    def freeze_prefix(self, fp: str, system_prompt: str, tools: Optional[List[Dict]]):
        tools_json = self._sorted_tools_json(tools)
        if fp in self._system_prompts:
            if self._system_prompts[fp] != system_prompt:
                logger.warning(
                    f"PREFIX CONFLICT | fp={fp} | system prompt changed after freeze "
                    f"-> proceeding but cache may miss"
                )
        else:
            self._system_prompts[fp] = system_prompt
            logger.info(f"PREFIX FREEZE | fp={fp} | system prompt locked ({len(system_prompt)} chars)")
        if fp in self._tool_specs:
            if self._tool_specs[fp] != tools_json:
                logger.warning(
                    f"PREFIX CONFLICT | fp={fp} | tool definitions changed after freeze "
                    f"-> proceeding but cache may miss"
                )
        else:
            self._tool_specs[fp] = tools_json
            tool_count = len(tools) if tools else 0
            logger.info(f"PREFIX FREEZE | fp={fp} | {tool_count} tool specs locked (sorted)")

    def append_messages(self, fp: str, messages: List[Dict]):
        if fp not in self._message_logs:
            self._message_logs[fp] = []
        self._message_logs[fp].extend(messages)


cache = ConversationCache()


# --- Stats (缓存命中/成本统计与熔断记�? ---
# 该模块负责追踪整个系统的 Token 消耗，�?/subclaw 的高并发任务提供数据支撑�?# 通过记录 input/output/cache_read tokens，系统能够精确计算并实现防破产熔断（Circuit Breaker）�?
class Stats:
    def __init__(self):
        self.requests: int = 0
        self.cache_read_tokens: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.start_time: float = time.time()
        self._cost_per_cache_token_cny: float = 0.00000098
        self.count_tokens_requests: int = 0
        self.upstream_retries: int = 0
        self.upstream_5xx: int = 0
        self.upstream_429: int = 0
        self.classifier_degraded: int = 0
        self.empty_text_blocks_dropped: int = 0
        self.format_repairs: int = 0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total) if total > 0 else 0.0

    @property
    def cost_saved_cny(self) -> float:
        return self.cache_read_tokens * self._cost_per_cache_token_cny

    def to_dict(self) -> Dict:
        return {
            "requests": self.requests,
            "cache_hit_rate": f"{self.cache_hit_rate:.2%}",
            "tokens": {
                "cache_read": self.cache_read_tokens,
                "input": self.input_tokens,
                "output": self.output_tokens,
            },
            "cost_saved_cny": round(self.cost_saved_cny, 6),
            "compat_layer": {
                "count_tokens_requests": self.count_tokens_requests,
                "upstream_retries": self.upstream_retries,
                "upstream_5xx": self.upstream_5xx,
                "upstream_429": self.upstream_429,
                "classifier_degraded": self.classifier_degraded,
                "empty_text_blocks_dropped": self.empty_text_blocks_dropped,
                "format_repairs": self.format_repairs,
            },
            "key_pool": {
                "total": len(CLAW_KEYS),
                "keys": [
                    {
                        "suffix": e["key"][-6:],
                        "url": e["url"],
                        "active_sessions": session_count_for_key(i),
                    }
                    for i, e in enumerate(CLAW_KEYS)
                ],
            },
            "uptime_seconds": int(time.time() - self.start_time),
        }


stats = Stats()


# --- Sanitize (empty-text-block 兼容) ---

def sanitize_anthropic_messages(messages: List[Dict]) -> List[Dict]:
    cleaned: List[Dict] = []
    dropped = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and not (block.get("text") or "").strip()
                ):
                    dropped += 1
                    continue
                new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content if new_content else [{"type": "text", "text": " "}]
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)
    if dropped:
        stats.empty_text_blocks_dropped += dropped
        logger.info(f"SANITIZE | dropped {dropped} empty text block(s)")
    return cleaned


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(len(text) / 4 * CLAUDE_TOKEN_MULTIPLIER)


def estimate_messages_tokens(system: Any, messages: List[Dict], tools: Optional[List[Dict]]) -> int:
    total = 0
    if isinstance(system, str):
        total += estimate_tokens(system)
    elif isinstance(system, list):
        for block in system:
            total += estimate_tokens(block.get("text", ""))
    for msg in messages or []:
        c = msg.get("content")
        if isinstance(c, str):
            total += estimate_tokens(c)
        elif isinstance(c, list):
            for block in c:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    total += estimate_tokens(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    total += estimate_tokens(json.dumps(block.get("input", {}), ensure_ascii=False))
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        for b in inner:
                            total += estimate_tokens(b.get("text", ""))
                    else:
                        total += estimate_tokens(str(inner))
    for tool in tools or []:
        total += estimate_tokens(json.dumps(tool, ensure_ascii=False))
    return total


def _passthrough_headers(req_headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key in ("x-request-id", "anthropic-version", "anthropic-beta", "user-agent"):
        v = req_headers.get(key)
        if v:
            out[key] = v
    return out


def _session_id_from(request: "Request") -> str:
    """优先�?x-session-id �?(worker 注入, 稳定); 回退 ip:port (TCP 连接, 易碎)�?""
    sid = request.headers.get("x-session-id")
    if sid and sid.strip():
        return sid.strip()
    c = request.client
    return f"{c.host}:{c.port}" if c else "unknown"


def _anthropic_upstream_headers(key: str, req_headers) -> Dict[str, str]:
    """原生 Anthropic 直�? Bearer 鉴权 + 透传 anthropic-version/beta�?""
    out = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "anthropic-version": req_headers.get("anthropic-version", "2023-06-01"),
    }
    out.update(_passthrough_headers(req_headers))
    return out


def _looks_degraded(status: int, body_text: str) -> bool:
    if status == 429 or 500 <= status <= 599:
        return True
    if status >= 400 and _DEGRADATION_PATTERNS.search(body_text or ""):
        return True
    return False


# --- Anthropic <-> OpenAI 格式转换 ---

def anthropic_to_openai(body: Dict) -> Dict:
    messages_in = sanitize_anthropic_messages(body.get("messages", []))
    system = body.get("system", "")
    openai_messages: List[Dict] = []
    if system:
        if isinstance(system, list):
            system_text = "\n".join(
                b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            system_text = str(system)
        openai_messages.append({"role": "system", "content": system_text})
    for msg in messages_in:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
        elif isinstance(content, list):
            parts: List[str] = []
            tool_results: List[Dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(f"[Tool call: {block.get('name', '?')}]\n{json.dumps(block.get('input', {}), ensure_ascii=False)}")
                elif btype == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner_text = "\n".join(b.get("text", "") for b in inner if isinstance(b, dict))
                    else:
                        inner_text = str(inner)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": inner_text,
                    })
            if parts:
                openai_messages.append({"role": role, "content": "\n".join(parts)})
            openai_messages.extend(tool_results)
        else:
            openai_messages.append({"role": role, "content": str(content or "")})
    tools = body.get("tools")
    openai_tools = None
    if tools:
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
    result: Dict[str, Any] = {
        "model": body.get("model", "default"),
        "messages": openai_messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
        "stream": body.get("stream", False),
    }
    if openai_tools:
        result["tools"] = openai_tools
    return result


def openai_to_anthropic_chunk(chunk_data: Dict, model: str) -> Optional[Dict]:
    if chunk_data.get("object") == "chat.completion.chunk":
        choices = chunk_data.get("choices", [])
        if not choices:
            return None
        choice = choices[0]
        delta = choice.get("delta", {})
        finish = choice.get("finish_reason")
        if "content" in delta:
            return {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta["content"]}}
        if finish == "stop":
            return {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}}
    return None


def openai_response_to_anthropic(resp: Dict, model: str) -> Dict:
    choices = resp.get("choices", [])
    content = []
    if choices:
        msg = choices[0].get("message", {})
        text = msg.get("content", "")
        if text:
            content.append({"type": "text", "text": text})
    usage = resp.get("usage", {})
    return {
        "id": resp.get("id", "msg-claw"),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cache_read_input_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
        },
    }


# --- 带重试的请求 ---

async def _post_with_retry(
    url: str, payload: Dict, headers: Dict[str, str]
) -> Tuple[int, bytes, Dict[str, str]]:
    last_status = 0
    last_body = b""
    last_headers: Dict[str, str] = {}
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)
                body_text = resp.text
                last_status = resp.status_code
                last_body = resp.content
                last_headers = dict(resp.headers)
                if resp.status_code < 400:
                    return last_status, last_body, last_headers
                if not _looks_degraded(resp.status_code, body_text):
                    return last_status, last_body, last_headers
                stats.upstream_retries += 1
                if resp.status_code == 429:
                    stats.upstream_429 += 1
                elif resp.status_code >= 500:
                    stats.upstream_5xx += 1
                if _DEGRADATION_PATTERNS.search(body_text or ""):
                    stats.classifier_degraded += 1
                if attempt < RETRY_MAX_ATTEMPTS - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"RETRY {attempt+1}/{RETRY_MAX_ATTEMPTS} | status={resp.status_code} | "
                        f"delay={delay:.1f}s | body={body_text[:200]!r}"
                    )
                    await asyncio.sleep(delay)
        except httpx.ConnectError as e:
            last_status = 0
            last_body = json.dumps({"error": {"message": str(e)}}).encode()
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(f"RETRY {attempt+1}/{RETRY_MAX_ATTEMPTS} | connect error: {e} | delay={delay:.1f}s")
                await asyncio.sleep(delay)
        except httpx.TimeoutException as e:
            last_status = 0
            last_body = json.dumps({"error": {"message": str(e)}}).encode()
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(f"RETRY {attempt+1}/{RETRY_MAX_ATTEMPTS} | timeout: {e} | delay={delay:.1f}s")
                await asyncio.sleep(delay)
    return last_status, last_body, last_headers


# --- Anthropic 原生直�?(保留 tool_use, 不做 OpenAI 转换) ---

async def _passthrough_post_once(
    url: str, payload: Dict, headers: Dict[str, str]
) -> Tuple[int, bytes, Dict[str, str]]:
    """单次 POST, 不重�?(429 由上层换 key 处理)�?""
    try:
        async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            return resp.status_code, resp.content, dict(resp.headers)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return 0, json.dumps({"error": {"type": "api_error", "message": str(e)}}).encode(), {}


def _key_order_on_failure(session_id: str, model: str, current_idx: int) -> List[int]:
    """429/5xx 时换 key 的尝试顺�? 当前 key 之后的同 model key (primary 优先)�?""
    order = routing_for_model(model)
    if current_idx in order:
        pos = order.index(current_idx)
        return order[pos + 1:] + order[:pos]
    return order


# --- 上游健康检�?---
_upstream_healthy = True
_upstream_last_success: float = 0


async def _check_upstream_health() -> bool:
    global _upstream_healthy, _upstream_last_success
    if _upstream_last_success > 0 and (time.time() - _upstream_last_success) < 60:
        return _upstream_healthy
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.options(f"{CLAW_KEYS[0]['url']}/v1/chat/completions")
            _upstream_healthy = True
            _upstream_last_success = time.time()
            return True
    except Exception:
        if _upstream_last_success > 0 and (time.time() - _upstream_last_success) < 300:
            _upstream_healthy = True
            return True
        _upstream_healthy = False
        return False


# --- FastAPI lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Claw-Proxy (Multi-Model Gateway) starting on port {PROXY_PORT}")
    logger.info(
        f"Retry: max={RETRY_MAX_ATTEMPTS} base_delay={RETRY_BASE_DELAY}s"
        f" | timeout={REQUEST_TIMEOUT_SEC}s"
        f" | token_mult={CLAUDE_TOKEN_MULTIPLIER}"
    )
    logger.info(f"Key pool: {len(CLAW_KEYS)} key(s) (session-affinity load balancing)")
    for i, entry in enumerate(CLAW_KEYS):
        tier = entry.get("tier", "primary")
        models = entry.get("models", ["*"])
        logger.info(f"  [{i}] {tier:8s} ...{entry['key'][-6:]} -> {entry['url']} models={models}")
    logger.info(f"Session timeout: {_SESSION_TIMEOUT}s")

    # 后台定时清理过期 session (防止系统空闲时内存泄�?
    async def _background_cleanup():
        while True:
            await asyncio.sleep(300)  # �?5 分钟清理一�?            now = time.time()
            with _session_lock:
                before = len(_session_map)
                _release_stale(now)
                after = len(_session_map)
                if before > after:
                    logger.info(f"Background cleanup: released {before - after} stale sessions ({after} active)")

    cleanup_task = asyncio.create_task(_background_cleanup())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Claw-Proxy shutting down")


app = FastAPI(title="Claw-Proxy: Multi-Model Agent Gateway", lifespan=lifespan)

# --- Dashboard Endpoints ---

@app.get("/api/status")
async def api_status():
    global _latest_model
    st = stats.to_dict()
    # attach active sessions details
    sessions = []
    now = time.time()
    for sid, info in _session_state.items():
        if now - info.get("last_active", 0) < 300:
            uptime = int(now - info.get("start_time", now))
            sessions.append({
                "session_id": sid,
                "model": info.get("model", ""),
                "action": info.get("action", ""),
                "key_suffix": info.get("key", ""),
                "uptime_seconds": uptime
            })
    st["active_subclaws"] = sessions
    st["orchestrator"] = f"[{_latest_model}]"
    return JSONResponse(content=st)

@app.get("/board")
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard HTML not found</h1>")

# NOTE: /models endpoint is defined once below (list_models, dynamic from keys.json).
# A second hardcoded /models definition used to live here but was dead code �?# FastAPI uses the FIRST registered route for duplicate paths, which made the
# dynamic one unreachable and forced default_model to a model not in keys.json.

# --- OpenAI 端点 ---

@app.post("/v1/chat/completions")
async def proxy_openai_completions(request: Request):
    global _upstream_last_success, _upstream_healthy
    start_time = time.time()
    try:
        body = await request.body()
        openai_req = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"REQUEST ERROR | invalid JSON: {e}")
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})

    model = openai_req.get("model", "default")
    is_stream = openai_req.get("stream", False)
    msg_count = len(openai_req.get("messages", []))

    entry = CLAW_KEYS[pick_key_for_session(_session_id_from(request), model)]
    current_key = entry["key"]
    upstream_base = entry["url"]
    logger.info(
        f"-> POST /v1/chat/completions | model={model} | msgs={msg_count} | "
        f"stream={is_stream} | key=...{current_key[-6:]}"
    )
    stats.requests += 1

    upstream_url = f"{upstream_base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {current_key}",
        "Content-Type": "application/json",
    }
    headers.update(_passthrough_headers(request.headers))

    try:
        if is_stream:
            return await _proxy_openai_streaming(upstream_url, headers, openai_req, start_time)
        return await _proxy_openai_non_streaming(upstream_url, headers, openai_req, start_time)
    except httpx.ConnectError as e:
        _upstream_healthy = False
        logger.error(f"UPSTREAM CONNECT ERROR | {e}")
        return JSONResponse(status_code=502, content={"error": {"message": f"Cannot connect to upstream: {upstream_base}"}})
    except httpx.TimeoutException as e:
        logger.error(f"UPSTREAM TIMEOUT | {e}")
        return JSONResponse(status_code=504, content={"error": {"message": "Upstream request timed out"}})
    except Exception as e:
        logger.error(f"UPSTREAM ERROR | {type(e).__name__}: {e}")
        return JSONResponse(status_code=502, content={"error": {"message": str(e)}})


async def _proxy_openai_non_streaming(
    upstream_url: str, headers: Dict, openai_req: Dict, start_time: float
) -> Response:
    global _upstream_last_success, _upstream_healthy

    status, body_bytes, _ = await _post_with_retry(upstream_url, openai_req, headers)
    if status == 0 or status >= 500:
        logger.error(f"UPSTREAM ERROR | status={status} | body={body_bytes[:500]!r}")
        return Response(
            content=body_bytes or json.dumps({"error": {"message": "Upstream unavailable"}}).encode(),
            status_code=status if status else 502,
            media_type="application/json",
        )

    _upstream_healthy = True
    _upstream_last_success = time.time()

    try:
        openai_resp = json.loads(body_bytes)
        usage = openai_resp.get("usage", {})
        cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        stats.input_tokens += usage.get("prompt_tokens", 0)
        stats.output_tokens += usage.get("completion_tokens", 0)
        stats.cache_read_tokens += cached_tokens
        if cached_tokens > 0:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(f"<- {status} | cache_read={cached_tokens} | {elapsed_ms}ms")
    except Exception:
        pass

    return Response(content=body_bytes, status_code=status, media_type="application/json")


async def _proxy_openai_streaming(
    upstream_url: str, headers: Dict, openai_req: Dict, start_time: float
) -> StreamingResponse:
    global _upstream_last_success, _upstream_healthy

    async def stream_generator():
        total_cached = 0
        total_input = 0
        total_output = 0
        try:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
                async with client.stream("POST", upstream_url, json=openai_req, headers=headers) as resp:
                    _upstream_healthy = True
                    _upstream_last_success = time.time()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            chunk = json.loads(data)
                            usage = chunk.get("usage", {})
                            if usage:
                                total_input += usage.get("prompt_tokens", 0)
                                total_output += usage.get("completion_tokens", 0)
                                total_cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", total_cached)
                            yield f"data: {json.dumps(chunk)}\n\n"
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"STREAM ERROR | {e}")

        stats.input_tokens += total_input
        stats.output_tokens += total_output
        stats.cache_read_tokens += total_cached
        if total_cached > 0:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"<- STREAM | cache_read={total_cached} | input={total_input} | "
            f"output={total_output} | {elapsed_ms}ms"
        )

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Anthropic 端点 ---
# --- Session State for Dashboard ---
_session_state: Dict[str, Dict] = {}
_latest_model: str = "claude-3-opus-20240229"

@app.post("/v1/messages")
async def proxy_anthropic_messages(request: Request):
    global _upstream_last_success, _upstream_healthy, _latest_model
    start_time = time.time()
    try:
        body = await request.body()
        anthropic_req = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"REQUEST ERROR | invalid JSON: {e}")
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})

    model = anthropic_req.get("model", "default")
    _latest_model = model
    is_stream = anthropic_req.get("stream", False)
    system_prompt = ""
    sys_raw = anthropic_req.get("system", "")
    if isinstance(sys_raw, str):
        system_prompt = sys_raw
    elif isinstance(sys_raw, list):
        system_prompt = "\n".join(b.get("text", "") for b in sys_raw if isinstance(b, dict) and b.get("type") == "text")

    messages = anthropic_req.get("messages", [])
    tools = anthropic_req.get("tools")

    fingerprint = cache.resolve_fingerprint(system_prompt, tools)
    cache.freeze_prefix(fingerprint, system_prompt, tools)
    cache.append_messages(fingerprint, messages)

    tool_count = len(tools) if tools else 0
    msg_count = len(messages)
    session_id = _session_id_from(request)
    key_idx = pick_key_for_session(session_id, model)
    entry = CLAW_KEYS[key_idx]
    protocol = entry.get("protocol", "anthropic")
    
    # --- MODEL REWRITE (Alias) ---
    allowed_models = entry.get("models")
    if allowed_models and model not in allowed_models:
        original_model = model
        model = allowed_models[0]
        anthropic_req["model"] = model
        logger.info(f"MODEL REWRITE | {original_model} -> {model} (for key ...{entry['key'][-6:]})")
    
    # --- Extract Latest Action for Dashboard ---
    latest_action = "思考中..."
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            c = msg.get("content", [])
            if isinstance(c, list):
                for b in reversed(c):
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        inp_str = json.dumps(b.get("input", {}), ensure_ascii=False)
                        if len(inp_str) > 80: inp_str = inp_str[:77] + "..."
                        latest_action = f"🛠�?工具: {b.get('name')} {inp_str}"
                        break
            if latest_action != "思考中...":
                break
    
    if session_id not in _session_state:
        _session_state[session_id] = {"start_time": time.time()}
    _session_state[session_id]["model"] = model
    _session_state[session_id]["action"] = latest_action
    _session_state[session_id]["last_active"] = time.time()
    _session_state[session_id]["key"] = entry["key"][-6:]
    
    # Clean stale sessions from dashboard state
    now = time.time()
    stale = [k for k, v in _session_state.items() if now - v["last_active"] > 300]
    for k in stale:
        _session_state.pop(k, None)

    logger.info(
        f"-> POST /v1/messages | model={model} | msgs={msg_count} | tools={tool_count} | "
        f"fp={fingerprint} | stream={is_stream} | sid={session_id} | "
        f"key=...{entry['key'][-6:]} | proto={protocol}"
    )
    stats.requests += 1

    # 原生 Anthropic 直�?(protocol="anthropic"): 转发原始 body, 保留 tool_use / tool_result�?    if protocol == "anthropic":
        try:
            if is_stream:
                return await _proxy_anthropic_passthrough_stream(
                    session_id, key_idx, model, anthropic_req, request.headers, start_time
                )
            return await _proxy_anthropic_passthrough(
                session_id, key_idx, model, anthropic_req, request.headers, start_time
            )
        except Exception as e:
            logger.error(f"PASSTHROUGH ERROR | {type(e).__name__}: {e}")
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "api_error", "message": str(e)}},
            )

    # 回退: OpenAI 协议上游 (会丢 tool_use, 仅用于纯文本模型)�?    current_key = entry["key"]
    upstream_base = entry["url"]
    openai_req = anthropic_to_openai(anthropic_req)
    upstream_url = f"{upstream_base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {current_key}",
        "Content-Type": "application/json",
    }
    headers.update(_passthrough_headers(request.headers))

    try:
        if is_stream:
            return await _proxy_streaming(upstream_url, headers, openai_req, model, fingerprint, start_time)
        return await _proxy_non_streaming(upstream_url, headers, openai_req, model, fingerprint, start_time)
    except httpx.ConnectError as e:
        _upstream_healthy = False
        logger.error(f"UPSTREAM CONNECT ERROR | {e}")
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "api_error", "message": f"Cannot connect to upstream: {upstream_base}"}},
        )
    except httpx.TimeoutException as e:
        logger.error(f"UPSTREAM TIMEOUT | {e}")
        return JSONResponse(
            status_code=504,
            content={"error": {"type": "api_error", "message": "Upstream request timed out"}},
        )
    except Exception as e:
        logger.error(f"UPSTREAM ERROR | {type(e).__name__}: {e}")
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "api_error", "message": str(e)}},
        )


async def _proxy_non_streaming(
    upstream_url: str, headers: Dict, openai_req: Dict, model: str, fingerprint: str, start_time: float
) -> Response:
    global _upstream_last_success, _upstream_healthy

    status, body_bytes, _ = await _post_with_retry(upstream_url, openai_req, headers)
    if status == 0 or status >= 500:
        logger.error(f"UPSTREAM ERROR | status={status} | body={body_bytes[:500]!r}")
        return Response(
            content=body_bytes or json.dumps({"error": {"message": "Upstream unavailable"}}).encode(),
            status_code=status if status else 502,
            media_type="application/json",
        )

    _upstream_healthy = True
    _upstream_last_success = time.time()

    try:
        openai_resp = json.loads(body_bytes)
        anthropic_resp = openai_response_to_anthropic(openai_resp, model)
        usage = anthropic_resp.get("usage", {})
        cached_tokens = usage.get("cache_read_input_tokens", 0)
        prompt_tokens = usage.get("input_tokens", 0)
        stats.input_tokens += prompt_tokens
        stats.output_tokens += usage.get("output_tokens", 0)
        stats.cache_read_tokens += cached_tokens
        if cached_tokens > 0:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1

        saved_cny = cached_tokens * stats._cost_per_cache_token_cny
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"<- 200 | cache_read={cached_tokens} | input={prompt_tokens} | "
            f"saved=¥{saved_cny:.6f} | {elapsed_ms}ms"
        )
        return Response(
            content=json.dumps(anthropic_resp).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )
    except Exception as e:
        logger.error(f"RESPONSE CONVERT ERROR | {e}")
        return Response(content=body_bytes, status_code=status, media_type="application/json")


async def _proxy_streaming(
    upstream_url: str, headers: Dict, openai_req: Dict, model: str, fingerprint: str, start_time: float
) -> StreamingResponse:
    async def stream_generator():
        total_cached = 0
        total_input = 0
        total_output = 0

        msg_id = f"msg-claw-{int(time.time()*1000)}"
        msg_start = {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model}}
        yield f"data: {json.dumps(msg_start)}\n\n"

        content_start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield f"data: {json.dumps(content_start)}\n\n"

        try:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT) as client:
                async with client.stream("POST", upstream_url, json=openai_req, headers=headers) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            usage = chunk.get("usage", {})
                            if usage:
                                total_input += usage.get("prompt_tokens", 0)
                                total_output += usage.get("completion_tokens", 0)
                                total_cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", total_cached)
                            anthropic_chunk = openai_to_anthropic_chunk(chunk, model)
                            if anthropic_chunk:
                                yield f"data: {json.dumps(anthropic_chunk)}\n\n"
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"STREAM ERROR | {e}")
            err_delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": f"\n[Stream error: {e}]"}}
            yield f"data: {json.dumps(err_delta)}\n\n"

        content_stop = {"type": "content_block_stop", "index": 0}
        yield f"data: {json.dumps(content_stop)}\n\n"

        stats.input_tokens += total_input
        stats.output_tokens += total_output
        stats.cache_read_tokens += total_cached
        if total_cached > 0:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1
        saved_cny = total_cached * stats._cost_per_cache_token_cny
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"<- STREAM | cache_read={total_cached} | input={total_input} | "
            f"output={total_output} | saved=¥{saved_cny:.6f} | {elapsed_ms}ms"
        )

        msg_delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": total_output}}
        yield f"data: {json.dumps(msg_delta)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _proxy_anthropic_passthrough(
    session_id: str, key_idx: int, model: str, anthropic_req: Dict, req_headers, start_time: float
) -> Response:
    """非流式原生直�?+ 429/5xx �?key 重试。body 原样转发, tool_use 保留�?""
    global _upstream_last_success, _upstream_healthy

    tried: List[int] = []
    queue = [key_idx] + _key_order_on_failure(session_id, model, key_idx)
    status, body_bytes = 0, b""

    for idx in queue:
        entry = CLAW_KEYS[idx]
        url = f"{entry['url']}/v1/messages"
        headers = _anthropic_upstream_headers(entry["key"], req_headers)
        status, body_bytes, _ = await _passthrough_post_once(url, anthropic_req, headers)
        tried.append(idx)

        if status == 200:
            if idx != key_idx:
                rebind_session(session_id, idx)
            _upstream_healthy = True
            _upstream_last_success = time.time()
            try:
                resp_json = json.loads(body_bytes)
                usage = resp_json.get("usage", {})
                cached = usage.get("cache_read_input_tokens", 0)
                stats.input_tokens += usage.get("input_tokens", 0)
                stats.output_tokens += usage.get("output_tokens", 0)
                stats.cache_read_tokens += cached
                stats.cache_hits += 1 if cached > 0 else 0
                stats.cache_misses += 0 if cached > 0 else 1
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f"<- 200 passthrough | key=...{entry['key'][-6:]} | cache_read={cached} | {elapsed_ms}ms")
            except Exception:
                pass
            return Response(content=body_bytes, status_code=200, media_type="application/json")

        if status == 429:
            stats.upstream_429 += 1
            stats.upstream_retries += 1
            logger.warning(f"429 on key[{idx}] ...{entry['key'][-6:]} -> failover to next key")
            continue
        # 5xx: 上游服务故障, 所�?key 指向同一上游, 切换无效, 直接返回给客户端
        if status >= 500:
            stats.upstream_5xx += 1
            logger.warning(f"5xx on key[{idx}] (upstream service error, no failover) -> return to client")
            return Response(content=body_bytes, status_code=status, media_type="application/json")
        # status == 0: 连接失败, 可能是网络问�? 尝试下一�?key
        if status == 0:
            stats.upstream_retries += 1
            logger.warning(f"status=0 on key[{idx}] (connect failed) -> failover")
            continue
        # 4xx (�?429): 客户端错�? �?key 也没�? 直接回传�?        logger.info(f"<- {status} passthrough (client error, no failover) | key=...{entry['key'][-6:]}")
        return Response(content=body_bytes, status_code=status, media_type="application/json")

    logger.error(f"ALL KEYS EXHAUSTED | tried={tried} | last_status={status}")
    return Response(
        content=body_bytes or json.dumps({"error": {"type": "api_error", "message": "all upstream keys failed"}}).encode(),
        status_code=status if status else 502,
        media_type="application/json",
    )


async def _proxy_anthropic_passthrough_stream(
    session_id: str, key_idx: int, model: str, anthropic_req: Dict, req_headers, start_time: float
) -> Response:
    """流式原生直�? 探测首响应状�? 429/5xx �?key; 成功后原样转�?SSE (保留 tool_use 事件)�?""
    global _upstream_last_success, _upstream_healthy

    queue = [key_idx] + _key_order_on_failure(session_id, model, key_idx)

    for idx in queue:
        entry = CLAW_KEYS[idx]
        url = f"{entry['url']}/v1/messages"
        headers = _anthropic_upstream_headers(entry["key"], req_headers)
        client = httpx.AsyncClient(timeout=_HTTPX_TIMEOUT)
        try:
            req = client.build_request("POST", url, json=anthropic_req, headers=headers)
            resp = await client.send(req, stream=True)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            await client.aclose()
            stats.upstream_retries += 1
            logger.warning(f"stream connect fail key[{idx}]: {e} -> failover")
            continue

        if resp.status_code == 429:
            stats.upstream_429 += 1
            stats.upstream_retries += 1
            await resp.aclose()
            await client.aclose()
            logger.warning(f"stream 429 key[{idx}] -> failover")
            continue

        # 5xx: 上游服务故障, 所�?key 指向同一上游, 切换无效, 直接返回给客户端
        if resp.status_code >= 500:
            stats.upstream_5xx += 1
            body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            logger.warning(f"stream {resp.status_code} key[{idx}] (upstream service error, no failover) -> return to client")
            return Response(content=body, status_code=resp.status_code, media_type="application/json")

        if resp.status_code != 200:
            body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            return Response(content=body, status_code=resp.status_code, media_type="application/json")

        if idx != key_idx:
            rebind_session(session_id, idx)
        _upstream_healthy = True
        _upstream_last_success = time.time()
        chosen = entry

        async def stream_generator():
            last_data_at = time.time()
            try:
                async for chunk in resp.aiter_raw():
                    if chunk:
                        last_data_at = time.time()
                        yield chunk
                    else:
                        # Empty chunk: check idle timeout (detects dead TUN VPN connections)
                        if time.time() - last_data_at > _STREAM_IDLE_TIMEOUT_SEC:
                            logger.error(f"STREAM IDLE TIMEOUT | no data for {_STREAM_IDLE_TIMEOUT_SEC}s, closing")
                            # Send a proper SSE error event so Claude Code gets a parseable frame
                            err_event = f'\nevent: error\ndata: {{"type":"error","error":{{"type":"api_error","message":"Stream idle timeout ({_STREAM_IDLE_TIMEOUT_SEC}s) �?proxy detected dead connection"}}}}\n\n'
                            yield err_event.encode("utf-8")
                            break
            except httpx.ReadError as e:
                logger.error(f"STREAM READ ERROR (TUN/VPN disconnect?) | {e}")
                # Attempt to send error frame so client doesn't get truncated JSON
                try:
                    err_event = f'\nevent: error\ndata: {{"type":"error","error":{{"type":"api_error","message":"Upstream read error: {e}"}}}}\n\n'
                    yield err_event.encode("utf-8")
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"STREAM PASSTHROUGH ERROR | {type(e).__name__}: {e}")
                try:
                    err_event = f'\nevent: error\ndata: {{"type":"error","error":{{"type":"api_error","message":"Stream error: {e}"}}}}\n\n'
                    yield err_event.encode("utf-8")
                except Exception:
                    pass
            finally:
                await resp.aclose()
                await client.aclose()
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f"<- STREAM passthrough done | key=...{chosen['key'][-6:]} | {elapsed_ms}ms")

        media = resp.headers.get("content-type", "text/event-stream")
        return StreamingResponse(
            stream_generator(),
            media_type=media,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    logger.error(f"ALL KEYS EXHAUSTED (stream) | model={model}")
    return JSONResponse(
        status_code=502,
        content={"error": {"type": "api_error", "message": "all upstream keys failed (stream)"}},
    )


# --- count_tokens 兼容端点 ---
@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    stats.count_tokens_requests += 1
    try:
        body = await request.body()
        req = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})
    system = req.get("system", "")
    messages = req.get("messages", [])
    tools = req.get("tools")
    total = estimate_messages_tokens(system, messages, tools)
    return JSONResponse(content={"input_tokens": total})


# --- 调试/运维端点 ---

@app.get("/health")
async def health_check():
    upstream_ok = await _check_upstream_health()
    return JSONResponse(
        status_code=200 if upstream_ok else 503,
        content={
            "status": "ok" if upstream_ok else "degraded",
            "key_pool": {
                "total": len(CLAW_KEYS),
                "keys": [{"suffix": e["key"][-6:], "url": e["url"]} for e in CLAW_KEYS],
            },
            "upstream_healthy": upstream_ok,
            "upstream_last_success": _upstream_last_success,
            "timestamp": time.time(),
        },
    )


@app.get("/stats")
async def get_stats():
    return JSONResponse(content=stats.to_dict())


@app.get("/models")
async def list_models():
    """自描�? 可用模型 + 每个模型的上游协�?层级。技能文档只引用此接�? 不抄表�?""
    by_model: Dict[str, Dict[str, Any]] = {}
    for e in CLAW_KEYS:
        models = e.get("models") or ["*"]
        for m in models:
            slot = by_model.setdefault(m, {
                "id": m,
                "protocol": e.get("protocol", "anthropic"),
                "tiers": set(),
                "key_count": 0,
                "upstreams": set(),
            })
            slot["tiers"].add(e.get("tier", "primary"))
            slot["upstreams"].add(e["url"])
            slot["key_count"] += 1
    data = []
    for _m, slot in sorted(by_model.items()):
        data.append({
            "id": slot["id"],
            "protocol": slot["protocol"],
            "tiers": sorted(slot["tiers"]),
            "key_count": slot["key_count"],
            "upstreams": sorted(slot["upstreams"]),
            "passthrough": slot["protocol"] == "anthropic",
        })
    return JSONResponse(content={
        "object": "list",
        "default_model": next((m for e in CLAW_KEYS for m in e.get("models", [])), "mimo-v2.5-pro"),
        "note": "passthrough=true 模型保留 tool_use/tool_result (可跑 Read/Glob/Grep 等工�?; false 仅纯文本�?,
        "data": data,
    })


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, timeout_keep_alive=900)

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

LOG_FORMAT = "%(asctime)s [claw-proxy] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
logger = logging.getLogger("claw-proxy")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_PORT = int(os.getenv("PROXY_PORT", "4748"))
CLAW_KEYS_FILE = os.getenv("CLAW_KEYS_FILE") or os.path.join(APP_DIR, "keys.json")
REQUEST_TIMEOUT_SEC = float(os.getenv("REQUEST_TIMEOUT_SEC", "300"))
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "0.5"))
CLAUDE_TOKEN_MULTIPLIER = float(os.getenv("CLAUDE_TOKEN_MULTIPLIER", "1.15"))

# Orchestration-layer observability. The proxy does NOT write these files; it only
# reads them (best-effort) when an orchestrator (subclaw / codex skill) points at a
# reports dir. Configured via ORCH_REPORTS_DIR (absolute path). If unset, the
# /orchestration endpoint reports disabled and the dashboard hides the block.
ORCH_REPORTS_DIR = os.getenv("ORCH_REPORTS_DIR", "").strip()
ORCH_JUDGE_CAP = int(os.getenv("ORCH_JUDGE_CAP", "3"))

FALLBACK_KEYS = [
    {
        "key": "sk-placeholder-not-configured",
        "url": "https://api.example.invalid",
        "protocol": "openai",
        "tier": "primary",
        "models": [],
    }
]

MODEL_PROFILES: Dict[str, Dict[str, Any]] = {}
PROXY_SETTINGS: Dict[str, Any] = {}
CLAW_KEYS: List[Dict[str, Any]] = []
SESSION_BINDINGS: Dict[str, int] = {}
KEY_FAILURES: Dict[int, int] = {}
KEY_REQUESTS: Dict[int, int] = {}
RECENT_ROUTES = deque(maxlen=120)
RR_COUNTER = 0

DEGRADATION_PATTERNS = re.compile(
    r"(temporarily unavailable|safety classifier|classifier error|overloaded|"
    r"rate.?limit|try again|server error|internal error|upstream error|bad gateway)",
    re.IGNORECASE,
)


def is_real_key(key: str) -> bool:
    if not key or not key.startswith(("tp-", "sk-", "fe_", "fe-")):
        return False
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        return False
    return len(key) >= 20 and "placeholder" not in key.lower()


def normalize_key_entry(raw: Dict[str, Any], model_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = raw.get("key")
    url = raw.get("url")
    if not key or not url:
        return None
    models = raw.get("models") or ([] if model_id is None else [model_id])
    if isinstance(models, str):
        models = [models]
    return {
        "key": key,
        "url": url.rstrip("/"),
        "tier": raw.get("tier", "primary"),
        "protocol": raw.get("protocol", "anthropic"),
        "models": models or [],
        "alias": raw.get("alias"),
        "provider": raw.get("provider"),
    }


def load_keys_config() -> List[Dict[str, Any]]:
    global MODEL_PROFILES, PROXY_SETTINGS
    try:
        with open(CLAW_KEYS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logger.warning("keys config load failed (%s); using placeholder fallback", exc)
        MODEL_PROFILES = {}
        PROXY_SETTINGS = {}
        return list(FALLBACK_KEYS)

    keys: List[Dict[str, Any]] = []
    profiles: Dict[str, Any] = cfg.get("model_profiles", {}) or {}

    for raw in cfg.get("keys", []) or []:
        entry = normalize_key_entry(raw)
        if entry:
            keys.append(entry)

    # Also accept the newer example schema: top-level "models".
    for raw in cfg.get("models", []) or []:
        model_id = raw.get("model_id")
        entry = normalize_key_entry(raw, model_id=model_id)
        if entry:
            keys.append(entry)
        if model_id:
            profiles.setdefault(
                model_id,
                {
                    "tier": raw.get("tier", "balanced"),
                    "alias": raw.get("alias"),
                    "capabilities": raw.get("capabilities", {}) or {},
                    "limits": raw.get("limits", {}) or {},
                    "cost_per_1m_in": raw.get("cost_per_1m_in"),
                    "cost_per_1m_out": raw.get("cost_per_1m_out"),
                },
            )

    MODEL_PROFILES = profiles
    PROXY_SETTINGS = cfg.get("global_proxy_settings", {}) or {}
    return keys or list(FALLBACK_KEYS)


CLAW_KEYS = load_keys_config()


def entry_supports_model(entry: Dict[str, Any], model: str) -> bool:
    models = entry.get("models") or []
    return not models or model in models


def candidate_indices(model: str, include_overflow: bool = False) -> List[int]:
    candidates = [
        i
        for i, entry in enumerate(CLAW_KEYS)
        if is_real_key(entry.get("key", "")) and entry_supports_model(entry, model)
    ]
    if not candidates:
        candidates = [i for i, entry in enumerate(CLAW_KEYS) if is_real_key(entry.get("key", ""))]
    if not include_overflow:
        primary = [i for i in candidates if CLAW_KEYS[i].get("tier", "primary") == "primary"]
        if primary:
            candidates = primary
    return candidates


def session_from_request(request: Request, fallback: str = "") -> str:
    return (
        request.headers.get("x-session-id")
        or request.headers.get("x-codex-session-id")
        or request.headers.get("openai-conversation-id")
        or fallback
        or ""
    )


def session_from_body(request: Request, body: Dict[str, Any], prefix: str, fallback: str = "") -> str:
    session_id = session_from_request(request, fallback)
    if session_id:
        return session_id
    try:
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    except Exception:
        raw = str(time.time())
    digest = hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}-{digest}-{int(time.time() * 1000)}"


def select_key(
    model: str,
    session_id: str = "",
    exclude: Optional[List[int]] = None,
    include_overflow: bool = False,
) -> Tuple[int, Dict[str, Any]]:
    global RR_COUNTER
    excluded = set(exclude or [])
    candidates = [i for i in candidate_indices(model, include_overflow=include_overflow) if i not in excluded]
    if not candidates and not include_overflow:
        candidates = [i for i in candidate_indices(model, include_overflow=True) if i not in excluded]
    if not candidates:
        raise RuntimeError(f"No configured key supports model {model!r}")

    bound = SESSION_BINDINGS.get(session_id) if session_id else None
    if bound in candidates:
        return bound, CLAW_KEYS[bound]

    primary = [i for i in candidates if CLAW_KEYS[i].get("tier", "primary") == "primary"] or candidates
    idx = primary[RR_COUNTER % len(primary)]
    RR_COUNTER += 1
    if session_id:
        SESSION_BINDINGS[session_id] = idx
    return idx, CLAW_KEYS[idx]


def rebind_session(model: str, session_id: str, tried: List[int]) -> Tuple[int, Dict[str, Any]]:
    idx, entry = select_key(model, session_id=session_id, exclude=tried, include_overflow=True)
    if session_id:
        SESSION_BINDINGS[session_id] = idx
    return idx, entry


def route_event(client: str, path: str, model: str, key_idx: int, session_id: str, status: str, note: str = "") -> None:
    entry = CLAW_KEYS[key_idx] if 0 <= key_idx < len(CLAW_KEYS) else {}
    RECENT_ROUTES.appendleft(
        {
            "ts": time.time(),
            "client": client,
            "path": path,
            "model": model,
            "key_index": key_idx,
            "key_suffix": (entry.get("key") or "")[-6:],
            "tier": entry.get("tier", "primary"),
            "protocol": entry.get("protocol", "anthropic"),
            "session_id": session_id,
            "status": status,
            "note": note,
        }
    )


def provider_url(entry: Dict[str, Any], path: str) -> str:
    return entry["url"].rstrip("/") + path


def passthrough_headers(req_headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key in ("x-request-id", "anthropic-version", "anthropic-beta", "user-agent"):
        val = req_headers.get(key)
        if val:
            out[key] = val
    return out


def auth_headers(entry: Dict[str, Any], request: Request, protocol: str) -> Dict[str, str]:
    key = entry["key"]
    if protocol == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
            "Content-Type": "application/json",
        }
    else:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    headers.update(passthrough_headers(request.headers))
    return headers


def looks_degraded(status: int, body_text: str) -> bool:
    return status == 0 or status == 429 or 500 <= status <= 599 or bool(
        DEGRADATION_PATTERNS.search(body_text or "")
    )


class Stats:
    def __init__(self) -> None:
        self.requests = 0
        self.cache_read_tokens = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.count_tokens_requests = 0
        self.upstream_retries = 0
        self.upstream_5xx = 0
        self.upstream_429 = 0
        self.classifier_degraded = 0
        self.empty_text_blocks_dropped = 0
        self.format_repairs = 0
        self.start_time = time.time()
        self._cost_per_cache_token_cny = 0.00000098

    def to_dict(self) -> Dict[str, Any]:
        total_cache = self.cache_hits + self.cache_misses
        hit_rate = self.cache_hits / total_cache if total_cache else 0.0
        return {
            "requests": self.requests,
            "cache_hit_rate": f"{hit_rate:.2%}",
            "tokens": {
                "cache_read": self.cache_read_tokens,
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
            },
            "cost_saved_cny": round(self.cache_read_tokens * self._cost_per_cache_token_cny, 6),
            "compat_layer": {
                "count_tokens_requests": self.count_tokens_requests,
                "upstream_retries": self.upstream_retries,
                "upstream_5xx": self.upstream_5xx,
                "upstream_429": self.upstream_429,
                "classifier_degraded": self.classifier_degraded,
                "empty_text_blocks_dropped": self.empty_text_blocks_dropped,
                "format_repairs": self.format_repairs,
            },
            "uptime_seconds": int(time.time() - self.start_time),
        }


stats = Stats()


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / 4 * CLAUDE_TOKEN_MULTIPLIER)


def estimate_messages_tokens(system: Any, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> int:
    total = estimate_tokens(system if isinstance(system, str) else json.dumps(system or "", ensure_ascii=False))
    total += estimate_tokens(json.dumps(messages or [], ensure_ascii=False))
    total += estimate_tokens(json.dumps(tools or [], ensure_ascii=False))
    return total


def sanitize_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    dropped = 0
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and not (block.get("text") or "").strip():
                    dropped += 1
                    continue
                new_content.append(block)
            msg = dict(msg)
            msg["content"] = new_content or [{"type": "text", "text": " "}]
        cleaned.append(msg)
    if dropped:
        stats.empty_text_blocks_dropped += dropped
        logger.info("SANITIZE | dropped %s empty text blocks", dropped)
    return cleaned


def repair_tool_args(raw: str) -> Dict[str, Any]:
    if not raw or not str(raw).strip():
        stats.format_repairs += 1
        return {}
    cleaned = str(raw).strip()
    candidates = [
        cleaned,
        re.sub(r",\s*([}\]])", r"\1", cleaned),
        cleaned
        + ("}" * max(0, cleaned.count("{") - cleaned.count("}")))
        + ("]" * max(0, cleaned.count("[") - cleaned.count("]"))),
    ]
    for candidate in candidates:
        try:
            result = json.loads(candidate)
            if candidate != cleaned:
                stats.format_repairs += 1
            return result
        except Exception:
            pass
    if not cleaned.startswith("{"):
        try:
            stats.format_repairs += 1
            return json.loads("{" + cleaned + "}")
        except Exception:
            pass
    stats.format_repairs += 1
    return {}


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            typ = block.get("type")
            if typ in ("text", "input_text", "output_text"):
                parts.append(block.get("text", ""))
            elif typ == "input_image":
                parts.append("[image]")
        return "\n".join(p for p in parts if p)
    return str(content)


def anthropic_to_openai(body: Dict[str, Any]) -> Dict[str, Any]:
    messages_in = sanitize_anthropic_messages(body.get("messages", []))
    openai_messages: List[Dict[str, Any]] = []
    system = body.get("system", "")
    if system:
        openai_messages.append({"role": "system", "content": text_from_content(system)})
    for msg in messages_in:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []
        for block in content or []:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            typ = block.get("type")
            if typ == "text":
                text_parts.append(block.get("text", ""))
            elif typ == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
            elif typ == "tool_result":
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": text_from_content(block.get("content", "")),
                    }
                )
        if role == "assistant":
            item: Dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            openai_messages.append(item)
        elif tool_results:
            openai_messages.extend(tool_results)
        else:
            openai_messages.append({"role": role, "content": "\n".join(text_parts)})
    req: Dict[str, Any] = {"model": body.get("model", "mimo"), "messages": openai_messages or [{"role": "user", "content": ""}]}
    for src in ("max_tokens", "temperature", "top_p"):
        if src in body:
            req[src] = body[src]
    if body.get("stream"):
        req["stream"] = True
        req["stream_options"] = {"include_usage": True}
    tools = []
    for tool in body.get("tools", []) or []:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
        )
    if tools:
        tools.sort(key=lambda t: t["function"]["name"])
        req["tools"] = tools
    return req


def openai_to_anthropic_request(openai_req: Dict[str, Any]) -> Dict[str, Any]:
    system_parts: List[str] = []
    messages: List[Dict[str, Any]] = []
    for msg in openai_req.get("messages", []) or []:
        role = msg.get("role", "user")
        content = msg.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": str(content or ""),
                        }
                    ],
                }
            )
            continue
        blocks: List[Dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": str(content)})
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            raw_args = fn.get("arguments", "{}")
            try:
                tool_input = json.loads(raw_args) if raw_args else {}
            except Exception:
                tool_input = repair_tool_args(raw_args)
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{len(blocks)}",
                    "name": fn.get("name", ""),
                    "input": tool_input,
                }
            )
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": blocks or [{"type": "text", "text": " "}]})
    req: Dict[str, Any] = {
        "model": openai_req.get("model", "mimo"),
        "max_tokens": openai_req.get("max_tokens", 4096),
        "messages": messages,
    }
    if system_parts:
        req["system"] = "\n".join(system_parts)
    if openai_req.get("stream"):
        req["stream"] = True
    for key in ("temperature", "top_p"):
        if key in openai_req:
            req[key] = openai_req[key]
    tools = []
    for tool in openai_req.get("tools", []) or []:
        fn = tool.get("function", {}) or {}
        if fn.get("name"):
            tools.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
    if tools:
        tools.sort(key=lambda t: t["name"])
        req["tools"] = tools
    return req


def openai_to_anthropic_response(openai_resp: Dict[str, Any], model: str) -> Dict[str, Any]:
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    blocks: List[Dict[str, Any]] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {}) or {}
        raw = fn.get("arguments", "{}")
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            args = repair_tool_args(raw)
        blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{len(blocks)}",
                "name": fn.get("name", ""),
                "input": args,
            }
        )
    usage = openai_resp.get("usage", {}) or {}
    return {
        "id": openai_resp.get("id") or f"msg_{int(time.time())}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks or [{"type": "text", "text": " "}],
        "stop_reason": "tool_use" if any(b.get("type") == "tool_use" for b in blocks) else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            "cache_read_input_tokens": usage.get(
                "cache_read_input_tokens",
                usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            ),
        },
    }


def anthropic_to_openai_response(anthropic_resp: Dict[str, Any], model: str) -> Dict[str, Any]:
    text = ""
    tool_calls = []
    for block in anthropic_resp.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"call_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}) or {}, ensure_ascii=False),
                    },
                }
            )
    usage = anthropic_resp.get("usage", {}) or {}
    choice: Dict[str, Any] = {
        "index": 0,
        "message": {
            "role": "assistant",
            "content": text or None,
        },
        "finish_reason": "tool_calls" if tool_calls else "stop",
    }
    if tool_calls:
        choice["message"]["tool_calls"] = tool_calls
    return {
        "id": anthropic_resp.get("id") or f"chatcmpl_{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            "prompt_tokens_details": {
                "cached_tokens": usage.get("cache_read_input_tokens", 0),
            },
        },
    }


def responses_to_openai_chat(body: Dict[str, Any]) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": str(body["instructions"])})
    raw_input = body.get("input", [])
    input_items = [raw_input] if isinstance(raw_input, str) else (raw_input or [])
    for item in input_items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue
        typ = item.get("type")
        if typ in ("message", None):
            messages.append({"role": item.get("role", "user"), "content": text_from_content(item.get("content", ""))})
        elif typ == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.get("call_id") or item.get("id") or "call_0",
                            "type": "function",
                            "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                        }
                    ],
                }
            )
        elif typ in ("function_call_output", "tool_result"):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("tool_call_id") or "call_0",
                    "content": text_from_content(item.get("output", item.get("content", ""))),
                }
            )
    req: Dict[str, Any] = {"model": body.get("model", "mimo"), "messages": messages or [{"role": "user", "content": ""}]}
    if body.get("stream"):
        req["stream"] = True
        req["stream_options"] = {"include_usage": True}
    if body.get("max_output_tokens") is not None:
        req["max_tokens"] = body["max_output_tokens"]
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            req[key] = body[key]
    tools = []
    for tool in body.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") in ("function", "custom"):
            name = tool.get("name") or (tool.get("function", {}) or {}).get("name")
            if name:
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters")
                            or tool.get("input_schema")
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
        elif tool.get("type") in ("web_search_preview", "computer_use_preview"):
            continue
    if tools:
        req["tools"] = tools
    if body.get("tool_choice") and body.get("tool_choice") != "auto":
        req["tool_choice"] = body["tool_choice"]
    return req


def openai_chat_to_responses(openai_resp: Dict[str, Any], model: str) -> Dict[str, Any]:
    choice = (openai_resp.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    output: List[Dict[str, Any]] = []
    if msg.get("content"):
        text = msg["content"]
        output.append(
            {
                "type": "message",
                "id": f"msg_{hashlib.md5(text.encode()).hexdigest()[:12]}",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {}) or {}
        call_id = tc.get("id") or f"call_{len(output)}"
        output.append(
            {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}"),
                "status": "completed",
            }
        )
    usage = openai_resp.get("usage", {}) or {}
    return {
        "id": openai_resp.get("id") or f"resp_{int(time.time())}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output
        or [
            {
                "type": "message",
                "id": "msg_empty",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": " ", "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


async def post_with_failover(
    request: Request,
    path: str,
    payload: Dict[str, Any],
    model: str,
    session_id: str,
    client_name: str,
    protocol_override: Optional[str] = None,
) -> Tuple[int, bytes, Dict[str, str], int, Dict[str, Any]]:
    tried: List[int] = []
    idx, entry = select_key(model, session_id=session_id)
    last_status = 0
    last_body = b""
    last_headers: Dict[str, str] = {}
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        protocol = protocol_override or entry.get("protocol", "anthropic")
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                resp = await client.post(provider_url(entry, path), json=payload, headers=auth_headers(entry, request, protocol))
            last_status = resp.status_code
            last_body = resp.content
            last_headers = dict(resp.headers)
            body_text = last_body.decode("utf-8", errors="replace")[:2000]
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_status = 0
            last_body = str(exc).encode()
            last_headers = {}
            body_text = str(exc)
        KEY_REQUESTS[idx] = KEY_REQUESTS.get(idx, 0) + 1
        if 200 <= last_status < 300:
            KEY_FAILURES[idx] = 0
            route_event(client_name, path, model, idx, session_id, str(last_status))
            return last_status, last_body, last_headers, idx, entry
        if last_status == 429:
            stats.upstream_429 += 1
        elif last_status == 0 or 500 <= last_status <= 599:
            stats.upstream_5xx += 1
        if DEGRADATION_PATTERNS.search(body_text or ""):
            stats.classifier_degraded += 1
        KEY_FAILURES[idx] = KEY_FAILURES.get(idx, 0) + 1
        route_event(client_name, path, model, idx, session_id, f"retry:{last_status}", body_text[:160])
        if attempt >= RETRY_MAX_ATTEMPTS or not looks_degraded(last_status, body_text):
            return last_status, last_body, last_headers, idx, entry
        tried.append(idx)
        stats.upstream_retries += 1
        await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
        idx, entry = rebind_session(model, session_id, tried)
    return last_status, last_body, last_headers, idx, entry


def sse(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_error_bytes(message: str, event_type: str = "error") -> bytes:
    return sse(
        event_type,
        {"type": "error", "error": {"message": message}},
    ).encode("utf-8")


async def stream_openai_passthrough(request: Request, payload: Dict[str, Any], model: str, session_id: str, client_name: str) -> StreamingResponse:
    async def gen():
        idx, entry = select_key(model, session_id=session_id)
        route_event(client_name, "/v1/chat/completions", model, idx, session_id, "stream")
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                async with client.stream(
                    "POST",
                    provider_url(entry, "/v1/chat/completions"),
                    json=payload,
                    headers=auth_headers(entry, request, "openai"),
                ) as up:
                    if up.status_code != 200:
                        body = await up.aread()
                        route_event(client_name, "/v1/chat/completions", model, idx, session_id, f"error:{up.status_code}")
                        yield sse_error_bytes(body.decode("utf-8", errors="replace")[:2000])
                        return
                    async for chunk in up.aiter_raw():
                        yield chunk
        except Exception as exc:
            yield sse_error_bytes(str(exc))

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def stream_openai_from_anthropic(request: Request, payload: Dict[str, Any], model: str, session_id: str) -> StreamingResponse:
    async def gen():
        idx, entry = select_key(model, session_id=session_id)
        route_event("openai", "/v1/chat/completions", model, idx, session_id, "stream-convert")
        chunk_id = f"chatcmpl_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                async with client.stream(
                    "POST",
                    provider_url(entry, "/v1/messages"),
                    json=payload,
                    headers=auth_headers(entry, request, "anthropic"),
                ) as up:
                    if up.status_code != 200:
                        err = await up.aread()
                        yield f"data: {json.dumps({'error': {'message': err.decode('utf-8', errors='replace')[:500]}})}\n\n"
                        return
                    buffer = ""
                    tool_index = 0
                    async for chunk in up.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line == "data: [DONE]" or line.startswith("event:"):
                                continue
                            if not line.startswith("data: "):
                                continue
                            try:
                                data = json.loads(line[6:])
                            except Exception:
                                continue
                            typ = data.get("type")
                            if typ == "content_block_delta":
                                delta = data.get("delta", {}) or {}
                                if delta.get("type") == "text_delta":
                                    yield "data: " + json.dumps(
                                        {
                                            "id": chunk_id,
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": model,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {"content": delta.get("text", "")},
                                                    "finish_reason": None,
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ) + "\n\n"
                            elif typ == "content_block_start":
                                block = data.get("content_block", {}) or {}
                                if block.get("type") == "tool_use":
                                    call_id = block.get("id") or f"call_{tool_index}"
                                    yield "data: " + json.dumps(
                                        {
                                            "id": chunk_id,
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": model,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {
                                                        "tool_calls": [
                                                            {
                                                                "index": tool_index,
                                                                "id": call_id,
                                                                "type": "function",
                                                                "function": {
                                                                    "name": block.get("name", ""),
                                                                    "arguments": json.dumps(block.get("input", {}) or {}, ensure_ascii=False),
                                                                },
                                                            }
                                                        ]
                                                    },
                                                    "finish_reason": None,
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ) + "\n\n"
                                    tool_index += 1
                            elif typ == "message_delta":
                                delta = data.get("delta", {}) or {}
                                stop_reason = delta.get("stop_reason")
                                if stop_reason:
                                    yield "data: " + json.dumps(
                                        {
                                            "id": chunk_id,
                                            "object": "chat.completion.chunk",
                                            "created": int(time.time()),
                                            "model": model,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {},
                                                    "finish_reason": "tool_calls" if stop_reason == "tool_use" else "stop",
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ) + "\n\n"
                    yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': {'message': str(exc)}})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def stream_responses(request: Request, openai_req: Dict[str, Any], model: str, session_id: str) -> StreamingResponse:
    async def gen():
        resp_id = f"resp_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}"
        yield sse(
            "response.created",
            {"type": "response.created", "response": {"id": resp_id, "object": "response", "status": "in_progress", "model": model, "output": []}},
        )
        idx, entry = select_key(model, session_id=session_id)
        protocol = entry.get("protocol", "anthropic")
        if protocol == "anthropic":
            path = "/v1/messages"
            payload = openai_to_anthropic_request(openai_req)
            headers = auth_headers(entry, request, "anthropic")
        else:
            path = "/v1/chat/completions"
            payload = openai_req
            headers = auth_headers(entry, request, "openai")
        route_event("codex", "/v1/responses", model, idx, session_id, "stream")
        output_index = 0
        text_started = False
        tool_calls: Dict[int, Dict[str, str]] = {}
        total_input = total_output = total_cached = 0
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                async with client.stream("POST", provider_url(entry, path), json=payload, headers=headers) as up:
                    if up.status_code != 200:
                        err = await up.aread()
                        route_event("codex", "/v1/responses", model, idx, session_id, f"error:{up.status_code}")
                        yield sse(
                            "response.failed",
                            {
                                "type": "response.failed",
                                "response": {
                                    "id": resp_id,
                                    "status": "failed",
                                    "error": {"message": err.decode("utf-8", errors="replace")[:500]},
                                },
                            },
                        )
                        return
                    buffer = ""
                    async for chunk in up.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line == "data: [DONE]" or line.startswith("event:"):
                                continue
                            if not line.startswith("data: "):
                                continue
                            try:
                                data = json.loads(line[6:])
                            except Exception:
                                continue
                            if protocol == "anthropic":
                                typ = data.get("type")
                                if typ == "content_block_start":
                                    block = data.get("content_block", {}) or {}
                                    if block.get("type") == "text":
                                        text_started = True
                                        yield sse(
                                            "response.output_item.added",
                                            {
                                                "type": "response.output_item.added",
                                                "output_index": output_index,
                                                "item": {
                                                    "type": "message",
                                                    "id": f"msg_{output_index}",
                                                    "status": "in_progress",
                                                    "role": "assistant",
                                                    "content": [],
                                                },
                                            },
                                        )
                                        yield sse(
                                            "response.content_part.added",
                                            {
                                                "type": "response.content_part.added",
                                                "item_id": f"msg_{output_index}",
                                                "output_index": output_index,
                                                "content_index": 0,
                                                "part": {"type": "output_text", "text": "", "annotations": []},
                                            },
                                        )
                                    elif block.get("type") == "tool_use":
                                        call_id = block.get("id") or f"call_{output_index}"
                                        item = {
                                            "type": "function_call",
                                            "id": call_id,
                                            "call_id": call_id,
                                            "name": block.get("name", ""),
                                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                                            "status": "completed",
                                        }
                                        yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": item})
                                        yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": output_index, "item": item})
                                        output_index += 1
                                elif typ == "content_block_delta":
                                    delta = data.get("delta", {}) or {}
                                    if delta.get("type") == "text_delta":
                                        yield sse(
                                            "response.output_text.delta",
                                            {
                                                "type": "response.output_text.delta",
                                                "item_id": f"msg_{output_index}",
                                                "output_index": output_index,
                                                "content_index": 0,
                                                "delta": delta.get("text", ""),
                                            },
                                        )
                                elif typ == "content_block_stop" and text_started:
                                    yield sse("response.output_text.done", {"type": "response.output_text.done", "item_id": f"msg_{output_index}", "output_index": output_index, "content_index": 0, "text": ""})
                                    yield sse(
                                        "response.output_item.done",
                                        {
                                            "type": "response.output_item.done",
                                            "output_index": output_index,
                                            "item": {"type": "message", "id": f"msg_{output_index}", "status": "completed", "role": "assistant", "content": []},
                                        },
                                    )
                                    output_index += 1
                                    text_started = False
                                elif typ == "message_delta":
                                    total_output = (data.get("usage") or {}).get("output_tokens", total_output)
                                continue
                            if data.get("usage"):
                                usage = data["usage"]
                                total_input = usage.get("prompt_tokens", total_input)
                                total_output = usage.get("completion_tokens", total_output)
                                total_cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", total_cached)
                            choice = (data.get("choices") or [{}])[0]
                            delta = choice.get("delta", {}) or {}
                            if delta.get("content"):
                                if not text_started:
                                    text_started = True
                                    yield sse(
                                        "response.output_item.added",
                                        {
                                            "type": "response.output_item.added",
                                            "output_index": output_index,
                                            "item": {"type": "message", "id": f"msg_{output_index}", "status": "in_progress", "role": "assistant", "content": []},
                                        },
                                    )
                                    yield sse(
                                        "response.content_part.added",
                                        {
                                            "type": "response.content_part.added",
                                            "item_id": f"msg_{output_index}",
                                            "output_index": output_index,
                                            "content_index": 0,
                                            "part": {"type": "output_text", "text": "", "annotations": []},
                                        },
                                    )
                                yield sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": f"msg_{output_index}", "output_index": output_index, "content_index": 0, "delta": delta["content"]})
                            for tc_delta in delta.get("tool_calls", []) or []:
                                tc = tool_calls.setdefault(tc_delta.get("index", 0), {"id": "", "name": "", "arguments": ""})
                                if tc_delta.get("id"):
                                    tc["id"] = tc_delta["id"]
                                fn = tc_delta.get("function", {}) or {}
                                if fn.get("name"):
                                    tc["name"] = fn["name"]
                                if "arguments" in fn:
                                    tc["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason"):
                                if text_started:
                                    yield sse("response.output_text.done", {"type": "response.output_text.done", "item_id": f"msg_{output_index}", "output_index": output_index, "content_index": 0, "text": ""})
                                    yield sse(
                                        "response.output_item.done",
                                        {
                                            "type": "response.output_item.done",
                                            "output_index": output_index,
                                            "item": {"type": "message", "id": f"msg_{output_index}", "status": "completed", "role": "assistant", "content": []},
                                        },
                                    )
                                    output_index += 1
                                    text_started = False
                                for tc_idx in sorted(tool_calls):
                                    tc = tool_calls[tc_idx]
                                    call_id = tc.get("id") or f"call_{tc_idx}"
                                    item = {
                                        "type": "function_call",
                                        "id": call_id,
                                        "call_id": call_id,
                                        "name": tc.get("name", ""),
                                        "arguments": tc.get("arguments", "{}"),
                                        "status": "completed",
                                    }
                                    yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": item})
                                    yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": output_index, "item": item})
                                    output_index += 1
        except Exception as exc:
            yield sse("response.failed", {"type": "response.failed", "response": {"id": resp_id, "status": "failed", "error": {"message": str(exc)}}})
            return
        stats.input_tokens += total_input
        stats.output_tokens += total_output
        stats.cache_read_tokens += total_cached
        if total_cached > 0:
            stats.cache_hits += 1
        elif total_input or total_output:
            stats.cache_misses += 1
        yield sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "status": "completed",
                    "model": model,
                    "output": [],
                    "usage": {"input_tokens": total_input, "output_tokens": total_output, "total_tokens": total_input + total_output},
                },
            },
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def stream_anthropic_from_openai(request: Request, openai_req: Dict[str, Any], model: str, session_id: str) -> StreamingResponse:
    async def gen():
        idx, entry = select_key(model, session_id=session_id)
        route_event("anthropic", "/v1/messages", model, idx, session_id, "stream-convert")
        msg_id = f"msg_{hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}"
        content_idx = 0
        text_open = False
        tool_calls: Dict[int, Dict[str, str]] = {}
        yield sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                async with client.stream(
                    "POST",
                    provider_url(entry, "/v1/chat/completions"),
                    json=openai_req,
                    headers=auth_headers(entry, request, "openai"),
                ) as up:
                    if up.status_code != 200:
                        err = await up.aread()
                        yield sse("error", {"type": "api_error", "message": err.decode("utf-8", errors="replace")[:500]})
                        return
                    buffer = ""
                    async for chunk in up.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line == "data: [DONE]" or not line.startswith("data: "):
                                continue
                            try:
                                data = json.loads(line[6:])
                            except Exception:
                                continue
                            choice = (data.get("choices") or [{}])[0]
                            delta = choice.get("delta", {}) or {}
                            if delta.get("content"):
                                if not text_open:
                                    yield sse("content_block_start", {"type": "content_block_start", "index": content_idx, "content_block": {"type": "text", "text": ""}})
                                    text_open = True
                                yield sse("content_block_delta", {"type": "content_block_delta", "index": content_idx, "delta": {"type": "text_delta", "text": delta["content"]}})
                            for tc_delta in delta.get("tool_calls", []) or []:
                                tc = tool_calls.setdefault(tc_delta.get("index", 0), {"id": "", "name": "", "arguments": ""})
                                if tc_delta.get("id"):
                                    tc["id"] = tc_delta["id"]
                                fn = tc_delta.get("function", {}) or {}
                                if fn.get("name"):
                                    tc["name"] = fn["name"]
                                if "arguments" in fn:
                                    tc["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason"):
                                if text_open:
                                    yield sse("content_block_stop", {"type": "content_block_stop", "index": content_idx})
                                    content_idx += 1
                                    text_open = False
                                for tc_idx in sorted(tool_calls):
                                    tc = tool_calls[tc_idx]
                                    try:
                                        args = json.loads(tc.get("arguments") or "{}")
                                    except Exception:
                                        args = repair_tool_args(tc.get("arguments", "{}"))
                                    yield sse(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": content_idx,
                                            "content_block": {
                                                "type": "tool_use",
                                                "id": tc.get("id") or f"toolu_{tc_idx}",
                                                "name": tc.get("name", ""),
                                                "input": args,
                                            },
                                        },
                                    )
                                    yield sse("content_block_stop", {"type": "content_block_stop", "index": content_idx})
                                    content_idx += 1
                                yield sse(
                                    "message_delta",
                                    {
                                        "type": "message_delta",
                                        "delta": {"stop_reason": "tool_use" if tool_calls else "end_turn", "stop_sequence": None},
                                        "usage": {"output_tokens": 0},
                                    },
                                )
                                yield sse("message_stop", {"type": "message_stop"})
        except Exception as exc:
            yield sse("error", {"type": "api_error", "message": str(exc)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def status_payload() -> Dict[str, Any]:
    payload = stats.to_dict()
    payload.update(
        {
            "orchestrator": "Claw Proxy",
            "keys_config": CLAW_KEYS_FILE,
            "key_pool": {
                "total": len(CLAW_KEYS),
                "real": len([e for e in CLAW_KEYS if is_real_key(e.get("key", ""))]),
                "keys": [
                    {
                        "index": i,
                        "suffix": (e.get("key") or "")[-6:],
                        "url": e.get("url"),
                        "tier": e.get("tier", "primary"),
                        "protocol": e.get("protocol", "anthropic"),
                        "models": e.get("models", []),
                        "requests": KEY_REQUESTS.get(i, 0),
                        "failures": KEY_FAILURES.get(i, 0),
                        "real": is_real_key(e.get("key", "")),
                    }
                    for i, e in enumerate(CLAW_KEYS)
                ],
            },
            "sessions": {
                sid: {"key_index": idx, "key_suffix": (CLAW_KEYS[idx].get("key") or "")[-6:]}
                for sid, idx in list(SESSION_BINDINGS.items())[-40:]
                if 0 <= idx < len(CLAW_KEYS)
            },
            "recent_routes": list(RECENT_ROUTES),
            "active_subclaws": [r for r in list(RECENT_ROUTES)[:20] if r.get("session_id")],
            "orchestration_enabled": bool(ORCH_REPORTS_DIR) and os.path.isdir(
                os.path.join(APP_DIR, ORCH_REPORTS_DIR) if not os.path.isabs(ORCH_REPORTS_DIR) else ORCH_REPORTS_DIR
            ),
        }
    )
    return payload


async def health_upstream() -> bool:
    return any(is_real_key(entry.get("key", "")) for entry in CLAW_KEYS)


# ---------- Orchestration-layer observability (read-only) ----------

_JUDGE_VERDICT_RE = re.compile(r"JUDGE_VERDICT:\s*(TRUE|PARTIAL|FALSE)", re.IGNORECASE)


def _safe_reports_root(requested: str) -> Optional[str]:
    """Resolve a requested reports dir to an absolute path inside the configured
    ORCH_REPORTS_DIR root. Returns None if unconfigured, missing, or outside the root
    (path-traversal guard). The root itself may be relative — resolved against APP_DIR."""
    root = ORCH_REPORTS_DIR
    if not root:
        return None
    root_abs = os.path.realpath(os.path.join(APP_DIR, root)) if not os.path.isabs(root) else os.path.realpath(root)
    if not os.path.isdir(root_abs):
        return None
    target = requested or root_abs
    target_abs = os.path.realpath(target)
    if not (target_abs == root_abs or target_abs.startswith(root_abs + os.sep)):
        return None
    return target_abs if os.path.isdir(target_abs) else None


def _read_json_safe(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _latest_pool_status(reports_dir: str) -> Dict[str, Any]:
    best: Optional[Tuple[float, str]] = None
    try:
        for name in os.listdir(reports_dir):
            if not name.startswith("pool_status.") or not name.endswith(".json"):
                continue
            full = os.path.join(reports_dir, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, full)
    except OSError:
        pass
    return _read_json_safe(best[1]) if best else {}


def _read_worker_statuses(reports_dir: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        names = sorted(n for n in os.listdir(reports_dir) if n.startswith("worker_") and n.endswith(".status.json"))
    except OSError:
        return out
    for name in names:
        data = _read_json_safe(os.path.join(reports_dir, name))
        if data:
            out.append(data)
    return out


def _read_judge_verdicts(reports_dir: str, root_abs: str = "") -> List[Dict[str, Any]]:
    """Scan for judge transcripts and extract the latest verdict per task.

    Search locations (all bounded by root_abs when provided, to avoid reading
    outside the configured root):
      - reports_dir itself (judge transcripts occasionally land here)
      - <sibling of reports_dir>/judges  i.e. .ai_agents/judges  (canonical, per the judge brief)
      - <workdir>/runs  (legacy path, for older briefs)
    Returns a list sorted by mtime (newest first)."""
    verdicts: List[Dict[str, Any]] = []
    root_real = os.path.realpath(root_abs) if root_abs else ""
    reports_real = os.path.realpath(reports_dir)

    def _within_root(p: str) -> bool:
        if not root_real:
            return True
        pr = os.path.realpath(p)
        return pr == root_real or pr.startswith(root_real + os.sep)

    search_dirs: List[str] = [reports_dir]
    parent = os.path.dirname(reports_real)
    candidates = [
        os.path.join(parent, "judges"),     # .ai_agents/judges  (canonical)
        os.path.join(parent, "runs"),        # .ai_agents/runs   (legacy sibling)
    ]
    # workdir-root /runs: walk up from reports_dir to root, check runs/ at each level
    cand = reports_real
    for _ in range(3):
        cand = os.path.dirname(cand)
        candidates.append(os.path.join(cand, "runs"))
        if cand == root_real or os.path.dirname(cand) == cand:
            break

    for d in candidates:
        if not _within_root(d) or not os.path.isdir(d):
            continue
        if d in search_dirs:
            continue
        search_dirs.append(d)

    seen_paths: set = set()
    for d in search_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if "judge" not in name.lower() or not name.endswith(".md"):
                continue
            full = os.path.join(d, name)
            if full in seen_paths:
                continue
            seen_paths.add(full)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            # The judge brief requires 'End with EXACTLY one verdict line', so the
            # authoritative verdict is the LAST JUDGE_VERDICT: in the transcript —
            # not the first. A judge reasoning aloud may write 'JUDGE_VERDICT: FALSE
            # if X...' while discussing, before concluding 'JUDGE_VERDICT: TRUE'.
            # re.search would pick the discussion line; finditer + last picks the
            # real conclusion.
            matches = list(_JUDGE_VERDICT_RE.finditer(text))
            if not matches:
                continue
            m = matches[-1]
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                mtime = 0.0
            verdicts.append({
                "file": name,
                "verdict": m.group(1).upper(),
                "mtime": mtime,
                "path": full,
            })
    verdicts.sort(key=lambda v: v["mtime"], reverse=True)
    return verdicts


def _read_shared_mailbox(reports_dir: str, root_abs: str) -> List[Dict[str, Any]]:
    """List files in <workdir>/.ai_agents/shared/.

    The mailbox is a sibling of reports_dir under the same .ai_agents/ dir
    (reports_dir = .ai_agents/reports, mailbox = .ai_agents/shared), so we
    look at parent/shared directly — no upward walk needed. Bounded by root_abs
    to avoid reading outside the configured root (same guard as judge search).
    Returns [] if not found."""
    mailbox: List[Dict[str, Any]] = []
    root_real = os.path.realpath(root_abs)
    parent = os.path.dirname(os.path.realpath(reports_dir))
    shared = os.path.join(parent, "shared")
    shared_real = os.path.realpath(shared)
    if root_real and not (shared_real == root_real or shared_real.startswith(root_real + os.sep)):
        return mailbox
    if not os.path.isdir(shared):
        return mailbox
    try:
        for name in sorted(os.listdir(shared)):
            full = os.path.join(shared, name)
            if not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            mailbox.append({"name": name, "size": st.st_size, "modified": st.st_mtime})
    except OSError:
        pass
    return mailbox


def orchestration_payload(reports_dir: str) -> Dict[str, Any]:
    pool = _latest_pool_status(reports_dir)
    workers = _read_worker_statuses(reports_dir)
    root_abs = os.path.realpath(
        os.path.join(APP_DIR, ORCH_REPORTS_DIR) if not os.path.isabs(ORCH_REPORTS_DIR) else ORCH_REPORTS_DIR
    )
    judges = _read_judge_verdicts(reports_dir, root_abs)
    mailbox = _read_shared_mailbox(reports_dir, root_abs)
    # judge_round = highest round number seen across judge transcripts (parsed
    # from filenames like 'task-N-judge-<round>-<stamp>.md'). Falls back to the
    # count of distinct judge files if no round number is parseable. Using max
    # avoids a retry within the same round (which produces a new stamp and thus
    # a new file) inflating the counter past the cap and falsely flagging red.
    round_re = re.compile(r"judge[^\d]*(\d+)", re.IGNORECASE)
    max_round = 0
    parsed_any = False
    for j in judges:
        m = round_re.search(os.path.basename(j["file"]))
        if m:
            parsed_any = True
            try:
                max_round = max(max_round, int(m.group(1)))
            except ValueError:
                pass
    judge_round = max_round if parsed_any else len({j["file"] for j in judges})
    return {
        "enabled": True,
        "reports_dir": reports_dir,
        "orchestrator": pool.get("orchestrator", {}),
        "workers": workers,
        "judge_verdicts": judges[:10],
        "judge_round": judge_round,
        "judge_cap": ORCH_JUDGE_CAP,
        "shared_mailbox": mailbox[:50],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Claw proxy starting on port %s", PROXY_PORT)
    logger.info("Keys config: %s", CLAW_KEYS_FILE)
    logger.info(
        "Key pool: %s configured, %s real",
        len(CLAW_KEYS),
        len([k for k in CLAW_KEYS if is_real_key(k.get("key", ""))]),
    )
    yield
    logger.info("Claw proxy shutting down")


app = FastAPI(title="Claw Proxy", lifespan=lifespan)


@app.get("/")
async def root():
    return {"service": "claw-proxy", "ui": "/ui", "models": "/models", "stats": "/stats"}


@app.get("/ui")
async def ui():
    path = os.path.join(APP_DIR, "dashboard.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception as exc:
        return HTMLResponse(f"<pre>dashboard not available: {exc}</pre>", status_code=500)


@app.get("/api/status")
async def api_status():
    return JSONResponse(content=status_payload())


@app.get("/orchestration")
async def orchestration(reports_dir: str = ""):
    """Read-only view of orchestrator (subclaw / codex skill) state: pool status,
    worker statuses, judge verdicts + round counter, shared mailbox. The proxy does
    not write any of these files — it only reads from a configured reports dir.
    Query param ?reports_dir=<abs> overrides ORCH_REPORTS_DIR but must stay inside it."""
    # Distinguish the disabled reasons so the dashboard can tell the user *why*
    # (not configured vs. configured-but-missing vs. path-traversal-rejected),
    # instead of silently hiding the block.
    if not ORCH_REPORTS_DIR:
        return JSONResponse(content={
            "enabled": False, "reports_dir": None,
            "reason": "not_configured",
            "message": "ORCH_REPORTS_DIR env var is not set. Set it to the absolute path of your orchestrator reports dir (the one holding pool_status.*.json) to enable orchestration observability.",
        })
    root = ORCH_REPORTS_DIR
    root_abs = os.path.realpath(os.path.join(APP_DIR, root)) if not os.path.isabs(root) else os.path.realpath(root)
    if not os.path.isdir(root_abs):
        return JSONResponse(content={
            "enabled": False, "reports_dir": root,
            "reason": "dir_missing",
            "message": f"ORCH_REPORTS_DIR is set to {root!r} (resolved to {root_abs}) but that directory does not exist. Create it, or point ORCH_REPORTS_DIR at the real reports dir.",
        })
    safe = _safe_reports_root(reports_dir)
    if safe is None:
        return JSONResponse(content={
            "enabled": False, "reports_dir": root,
            "reason": "outside_root",
            "message": f"The requested reports_dir is outside the configured ORCH_REPORTS_DIR root ({root_abs}). Refused to read.",
        })
    return JSONResponse(content=orchestration_payload(safe))


@app.get("/models")
async def list_models():
    profiles = MODEL_PROFILES or {
        m: {"tier": "balanced", "capabilities": {"supports_tools": True}}
        for e in CLAW_KEYS
        for m in e.get("models", [])
    }
    data = []
    for mid, profile in profiles.items():
        caps = profile.get("capabilities", {}) or {}
        cap_list = ["text"]
        if caps.get("supports_tools", True):
            cap_list.append("tool_use")
        if caps.get("supports_vision"):
            cap_list.append("vision")
        tier = profile.get("tier", "balanced")
        primary_count = len(
            [
                e
                for e in CLAW_KEYS
                if is_real_key(e.get("key", ""))
                and e.get("tier", "primary") == "primary"
                and entry_supports_model(e, mid)
            ]
        )
        total_count = len(
            [
                e
                for e in CLAW_KEYS
                if is_real_key(e.get("key", "")) and entry_supports_model(e, mid)
            ]
        )
        overflow_count = max(0, total_count - primary_count)
        capacity = primary_count or total_count
        data.append(
            {
                "id": mid,
                "object": "model",
                "tier": tier,
                "tiers": [tier],
                "alias": profile.get("alias"),
                "key_count": capacity,
                "capacity": capacity,
                "overflow_keys": overflow_count,
                "cost_per_1m_in": profile.get("cost_per_1m_in"),
                "cost_per_1m_out": profile.get("cost_per_1m_out"),
                "capabilities": cap_list,
                "context_window": caps.get("context_window"),
                "max_concurrent": (profile.get("limits", {}) or {}).get("max_concurrent"),
            }
        )
    if not data:
        data.append(
            {
                "id": "mimo",
                "object": "model",
                "tier": "balanced",
                "tiers": ["balanced"],
                "key_count": len([e for e in CLAW_KEYS if is_real_key(e.get("key", ""))]),
                "capacity": len([e for e in CLAW_KEYS if is_real_key(e.get("key", ""))]),
                "capabilities": ["text"],
            }
        )
    return {
        "object": "list",
        "default_model": data[0]["id"] if data else "mimo",
        "data": data,
        "proxy": {
            "base_url": f"http://localhost:{PROXY_PORT}",
            "anthropic_base_url": f"http://localhost:{PROXY_PORT}",
            "openai_base_url": f"http://localhost:{PROXY_PORT}/v1",
            "codex_base_url": f"http://localhost:{PROXY_PORT}/v1",
            "wire_api": "responses",
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})
    model = body.get("model", "mimo")
    session_id = session_from_body(request, body, "chat")
    stats.requests += 1
    _, selected_entry = select_key(model, session_id=session_id)
    protocol = selected_entry.get("protocol", "anthropic")
    if protocol == "anthropic":
        payload = openai_to_anthropic_request(body)
        if body.get("stream"):
            return await stream_openai_from_anthropic(request, payload, model, session_id)
        status, raw, _, _, _ = await post_with_failover(
            request, "/v1/messages", payload, model, session_id, "openai", "anthropic"
        )
        if status != 200:
            return Response(content=raw, status_code=status if status else 502, media_type="application/json")
        anthropic_resp = json.loads(raw)
        return JSONResponse(content=anthropic_to_openai_response(anthropic_resp, model), status_code=200)
    if body.get("stream"):
        return await stream_openai_passthrough(request, body, model, session_id, "openai")
    status, raw, _, _, _ = await post_with_failover(
        request, "/v1/chat/completions", body, model, session_id, "openai", "openai"
    )
    if status != 200:
        return Response(content=raw, status_code=status if status else 502, media_type="application/json")
    try:
        usage = json.loads(raw).get("usage", {})
        stats.input_tokens += usage.get("prompt_tokens", 0)
        stats.output_tokens += usage.get("completion_tokens", 0)
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        stats.cache_read_tokens += cached
        stats.cache_hits += 1 if cached else 0
        stats.cache_misses += 0 if cached else 1
    except Exception:
        pass
    return Response(content=raw, status_code=200, media_type="application/json")


@app.post("/v1/messages")
async def messages(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error", "message": "Invalid JSON"}},
        )
    model = body.get("model", "mimo")
    session_id = session_from_body(request, body, "msg")
    stats.requests += 1
    key_idx, entry = select_key(model, session_id=session_id)
    protocol = entry.get("protocol", "anthropic")
    if protocol == "anthropic":
        payload = body
        path = "/v1/messages"
        proto = "anthropic"
    else:
        payload = anthropic_to_openai(body)
        path = "/v1/chat/completions"
        proto = "openai"
    if body.get("stream"):
        if proto == "anthropic":
            async def passthrough():
                route_event("anthropic", "/v1/messages", model, key_idx, session_id, "stream")
                try:
                    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
                        async with client.stream(
                            "POST", provider_url(entry, path), json=payload, headers=auth_headers(entry, request, proto)
                        ) as up:
                            if up.status_code != 200:
                                body = await up.aread()
                                route_event("anthropic", "/v1/messages", model, key_idx, session_id, f"error:{up.status_code}")
                                yield sse_error_bytes(body.decode("utf-8", errors="replace")[:2000])
                                return
                            async for chunk in up.aiter_raw():
                                yield chunk
                except Exception as exc:
                    yield sse_error_bytes(str(exc))

            return StreamingResponse(
                passthrough(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return await stream_anthropic_from_openai(request, payload, model, session_id)
    status, raw, _, _, _ = await post_with_failover(request, path, payload, model, session_id, "anthropic", proto)
    if status != 200:
        return Response(content=raw, status_code=status if status else 502, media_type="application/json")
    if proto == "anthropic":
        return Response(content=raw, status_code=200, media_type="application/json")
    openai_resp = json.loads(raw)
    return JSONResponse(content=openai_to_anthropic_response(openai_resp, model), status_code=200)


@app.post("/v1/responses")
async def responses(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})
    model = body.get("model", "mimo")
    session_id = session_from_body(request, body, "resp", body.get("prompt_cache_key", ""))
    openai_req = responses_to_openai_chat(body)
    stats.requests += 1
    if body.get("stream"):
        return await stream_responses(request, openai_req, model, session_id)
    idx, entry = select_key(model, session_id=session_id)
    protocol = entry.get("protocol", "anthropic")
    if protocol == "anthropic":
        payload = openai_to_anthropic_request(openai_req)
        status, raw, _, _, _ = await post_with_failover(
            request, "/v1/messages", payload, model, session_id, "codex", "anthropic"
        )
        if status != 200:
            return Response(content=raw, status_code=status if status else 502, media_type="application/json")
        anthropic_resp = json.loads(raw)
        text = ""
        tool_calls = []
        for block in anthropic_resp.get("content", []) or []:
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )
        openai_resp = {
            "id": anthropic_resp.get("id"),
            "choices": [
                {
                    "message": {"content": text, "tool_calls": tool_calls},
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": anthropic_resp.get("usage", {}),
        }
    else:
        openai_req["stream"] = False
        status, raw, _, _, _ = await post_with_failover(
            request, "/v1/chat/completions", openai_req, model, session_id, "codex", "openai"
        )
        if status != 200:
            return Response(content=raw, status_code=status if status else 502, media_type="application/json")
        openai_resp = json.loads(raw)
    return JSONResponse(content=openai_chat_to_responses(openai_resp, model), status_code=200)


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error", "message": "Invalid JSON"}},
        )
    stats.count_tokens_requests += 1
    est = estimate_messages_tokens(body.get("system", ""), body.get("messages", []), body.get("tools", []))
    return {"input_tokens": est}


@app.get("/health")
async def health():
    ok = await health_upstream()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "degraded",
            "key_pool": len(CLAW_KEYS),
            "real_keys": len([e for e in CLAW_KEYS if is_real_key(e.get("key", ""))]),
            "timestamp": time.time(),
        },
    )


@app.get("/stats")
async def get_stats():
    return JSONResponse(content=status_payload())


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT)


if __name__ == "__main__":
    run()

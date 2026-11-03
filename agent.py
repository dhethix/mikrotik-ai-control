#!/usr/bin/env python3
"""
MikroTik Agent - OpenRouter Edition
Support model gratis & berbayar via OpenRouter.
Fitur: Dry-run, Konfirmasi write, Tool khusus, CLI + HTTP.
"""

import os
import json
import sqlite3
import re
import uuid
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import time
from typing import Optional, Dict, List
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from openai import OpenAI
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
import uvicorn

load_dotenv()
console = Console()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").lower().strip()
if LLM_PROVIDER not in ("openrouter", "groq"):
    LLM_PROVIDER = "openrouter"

GROQ_FREE_MODELS = [
    # Free tier Nov 2026 — prioritaskan yang stabil tool-calling
    {"id": "qwen/qwen3.8-27b", "label": "Qwen3.8 27B (free, tools)", "context": "131K", "tools": True},
    {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B (free)", "context": "128K", "tools": True},
    {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B (free)", "context": "128K", "tools": True},
    {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B Instant", "context": "128K", "tools": True},
    {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (berbayar)", "context": "128K", "tools": True},
]

# Daftar model gratis OpenRouter (tool-calling) — cocok untuk agent MikroTik
FREE_MODELS = [
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "label": "Nemotron 3 Ultra", "context": "1M"},
    {"id": "nvidia/nemotron-3.5-lightning:free", "label": "Nemotron 3.5 Lightning", "context": "1M"},
    {"id": "nvidia/nemotron-3.5-super-vl:free", "label": "Nemotron 3.5 Super VL", "context": "1M"},
    {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "label": "Nemotron 3 Nano Reasoning", "context": "256K"},
    {"id": "google/gemma-4-31b-it:free", "label": "Gemma 4 31B IT", "context": "262K"},
    {"id": "google/gemma-4-26b-a4b-it:free", "label": "Gemma 4 26B A4B", "context": "262K"},
    {"id": "poolside/laguna-s-2.1:free", "label": "Laguna S 2.1", "context": "262K"},
    {"id": "poolside/laguna-xs-2.1:free", "label": "Laguna XS 2.1", "context": "262K"},
    {"id": "xingchen-agi/xing4.0-29b-a4b:free", "label": "Xing4.0 29B A4B", "context": "262K"},
    {"id": "dots-studio/dots-3-note-preview:free", "label": "Dots3 Note Preview", "context": "512K"},
    {"id": "cohere/north-mini-code:free", "label": "North Mini Code", "context": "256K"},
    {"id": "liquid/lfm-2.5-2.6b:free", "label": "LFM 2.5 2.6B", "context": "65K"},
    {"id": "recursive/flint-v1.0:free", "label": "Flint v1.0", "context": "262K"},
    {"id": "thinkingmachines/inkling:free", "label": "Inkling", "context": "1M"},
    {"id": "thinkingmachines/inkling-small:free", "label": "Inkling Small", "context": "1M"},
    {"id": "openrouter/free", "label": "OpenRouter Free Router", "context": "200K"},
]


MIKROTIK_HOST = os.getenv("MIKROTIK_HOST", "")  # optional; prefer UI/DB
MIKROTIK_USER = os.getenv("MIKROTIK_USER", "")
MIKROTIK_PASS = os.getenv("MIKROTIK_PASS", "")
MIKROTIK_PORT = os.getenv("MIKROTIK_PORT", "443")
VERIFY_SSL = os.getenv("VERIFY_SSL", "false").lower() == "true"

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
REQUIRE_CONFIRMATION = os.getenv("REQUIRE_CONFIRMATION", "true").lower() == "true"

MODE = os.getenv("MODE", "http").lower()
HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8000"))
API_TOKEN = os.getenv("API_TOKEN", "")

# Persistent storage (survives restart if /app/data di-mount volume)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
MEMORY_FILE = DATA_DIR / "memory.md"
MEMORY_CORE_FILE = DATA_DIR / "memory_core.md"
MEMORY_DETAIL_FILE = DATA_DIR / "memory_detail.md"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SESSION_META_FILE = DATA_DIR / "session_meta.json"
# sqlite (default) | file
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sqlite").lower().strip()
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", str(DATA_DIR / "data_agent.db")))
MEMORY_MAX_CHARS = int(os.getenv("MEMORY_MAX_CHARS", "12000"))
# Context window ke OpenRouter saja — history penuh tetap di SQLite
API_HISTORY_WINDOW = int(os.getenv("API_HISTORY_WINDOW", "40"))  # hanya window ke model; sesi di DB unlimited


def _api_window_default() -> int:
    """Groq free TPM ketat (~8k/min) — window lebih kecil agar request tidak 413."""
    if LLM_PROVIDER == "groq":
        return max(4, min(API_HISTORY_WINDOW, int(os.getenv("GROQ_API_HISTORY_WINDOW", "8"))))
    return max(8, API_HISTORY_WINDOW)


_WIB = timezone(timedelta(hours=7))

def now_wib_iso() -> str:
    return datetime.now(_WIB).isoformat(timespec="seconds")

PERSIST_SESSIONS = os.getenv("PERSIST_SESSIONS", "true").lower() == "true"

def _active_api_key() -> str:
    if LLM_PROVIDER == "groq":
        return (GROQ_API_KEY or "").strip()
    return (OPENROUTER_API_KEY or "").strip()


def _active_model() -> str:
    if LLM_PROVIDER == "groq":
        return GROQ_MODEL or "llama-3.3-70b-versatile"
    return OPENROUTER_MODEL or "nvidia/nemotron-3-ultra-550b-a55b:free"


def _provider_base_url() -> str:
    if LLM_PROVIDER == "groq":
        return "https://api.groq.com/openai/v1"
    return "https://openrouter.ai/api/v1"


def rebuild_client() -> None:
    global client
    key = _active_api_key()
    kwargs = {"api_key": key or "missing", "base_url": _provider_base_url()}
    if LLM_PROVIDER == "openrouter":
        kwargs["default_headers"] = {
            "HTTP-Referer": "https://github.com/mikrotik-openrouter-agent",
            "X-Title": "MikroTik OpenRouter Agent",
        }
    client = OpenAI(**kwargs)


rebuild_client()

pending_actions: Dict[str, Dict] = {}


class MikroTikClient:
    def __init__(self):
        self.session = requests.Session()
        self.reconfigure()

    def reconfigure(self) -> None:
        host = (MIKROTIK_HOST or "").strip() or "127.0.0.1"
        port = (MIKROTIK_PORT or "443").strip()
        self.base = f"https://{host}:{port}/rest"
        self.session.auth = ((MIKROTIK_USER or "").strip(), (MIKROTIK_PASS or ""))
        self.session.verify = VERIFY_SSL

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        url = f"{self.base}{path}"
        try:
            if method == "GET":
                r = self.session.get(url, timeout=20)
            elif method == "POST":
                r = self.session.post(url, json=data or {}, timeout=20)
            elif method == "PUT":
                r = self.session.put(url, json=data or {}, timeout=20)
            elif method == "PATCH":
                r = self.session.patch(url, json=data or {}, timeout=20)
            elif method == "DELETE":
                r = self.session.delete(url, timeout=20)
            else:
                return {"error": f"Method {method} tidak didukung"}
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code}", "detail": r.text}
            return r.json() if r.content else {"status": "ok"}
        except Exception as e:
            return {"error": str(e)}

    def get(self, path: str): return self._request("GET", path)
    def post(self, path: str, data: dict): return self._request("POST", path, data)
    def put(self, path: str, data: dict): return self._request("PUT", path, data)
    def patch(self, path: str, data: dict): return self._request("PATCH", path, data)
    def delete(self, path: str): return self._request("DELETE", path)


mt = MikroTikClient()

# --- Live metrics from MikroTik REST (NO OpenRouter tokens) ---
_metrics_cache = {"ts": 0.0, "data": None}
_iface_counters = {}  # name -> (rx_byte, tx_byte, ts)


def _to_int(v, default=0):
    try:
        return int(str(v).replace(" ", ""))
    except Exception:
        return default


def _fmt_bytes(n: int) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def collect_live_metrics() -> dict:
    """Direct RouterOS REST only — does not call OpenRouter."""
    import time
    now = time.time()
    # cache 5s to keep UI smooth & light on router
    if _metrics_cache["data"] is not None and now - _metrics_cache["ts"] < 2:
        return _metrics_cache["data"]

    res = mt.get("/system/resource")
    if isinstance(res, list) and res:
        res = res[0]
    if not isinstance(res, dict):
        res = {}

    cpu = _to_int(res.get("cpu-load") or res.get("cpu_load") or 0)
    cpu_cores = _to_int(
        res.get("cpu-count")
        or res.get("cpu_count")
        or res.get("cpu-cores")
        or res.get("cpu_cores")
        or 0
    )
    total_mem = _to_int(res.get("total-memory") or res.get("total_memory") or 0)
    free_mem = _to_int(res.get("free-memory") or res.get("free_memory") or 0)
    used_mem = max(total_mem - free_mem, 0)
    uptime = res.get("uptime") or "-"
    version = res.get("version") or "-"
    board = res.get("board-name") or res.get("board_name") or "-"
    identity = "-"
    try:
        idr = mt.get("/system/identity")
        if isinstance(idr, list) and idr:
            idr = idr[0]
        if isinstance(idr, dict):
            identity = str(idr.get("name") or idr.get("identity") or "-").strip() or "-"
    except Exception:
        identity = "-"

    ppp = mt.get("/ppp/active")
    if isinstance(ppp, dict) and ppp.get("error"):
        pppoe_count = 0
        ppp_err = ppp.get("error")
    elif isinstance(ppp, list):
        pppoe_count = len(ppp)
        ppp_err = None
    else:
        pppoe_count = 0
        ppp_err = "unknown"

    # throughput from interface byte counters (prefer ether1-WAN)
    ifaces = mt.get("/interface")
    rx_mbps = 0.0
    tx_mbps = 0.0
    wan_name = None
    if isinstance(ifaces, list):
        prefer = None
        for it in ifaces:
            name = str(it.get("name") or "")
            if name == "ether1-WAN" or name.lower().startswith("ether1"):
                prefer = it
                break
        if prefer is None:
            for it in ifaces:
                if str(it.get("type") or "") == "ether" and str(it.get("running")).lower() in ("true", "yes"):
                    prefer = it
                    break
        if prefer is not None:
            wan_name = prefer.get("name")
            rx_b = _to_int(prefer.get("rx-byte") or prefer.get("rx_byte") or 0)
            tx_b = _to_int(prefer.get("tx-byte") or prefer.get("tx_byte") or 0)
            key = wan_name or "wan"
            prev = _iface_counters.get(key)
            _iface_counters[key] = (rx_b, tx_b, now)
            if prev:
                dt = max(now - prev[2], 0.001)
                rx_mbps = max((rx_b - prev[0]) * 8 / dt / 1_000_000, 0)
                tx_mbps = max((tx_b - prev[1]) * 8 / dt / 1_000_000, 0)

    data = {
        "ok": not bool(res.get("error")),
        "cpu_load": cpu,
        "cpu_cores": cpu_cores,
        "cpu": cpu,
        "total_memory": total_mem,
        "free_memory": free_mem,
        "used_memory": used_mem,
        "total_memory_human": _fmt_bytes(total_mem),
        "free_memory_human": _fmt_bytes(free_mem),
        "used_memory_human": _fmt_bytes(used_mem),
        "uptime": uptime,
        "version": version,
        "board": board,
        "identity": identity,
        "pppoe_online": pppoe_count,
        "wan_interface": wan_name,
        "rx_mbps": round(rx_mbps, 2),
        "tx_mbps": round(tx_mbps, 2),
        "error": res.get("error") or ppp_err,
        "source": "mikrotik-rest",
        "ts": now,
    }
    _metrics_cache["ts"] = now
    _metrics_cache["data"] = data
    return data




TOOLS = [
    {"type": "function", "function": {"name": "get_system_resource", "description": "Resource router (CPU, RAM, uptime, version).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_interfaces", "description": "Daftar semua interface.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_ip_addresses", "description": "Daftar IP address.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_routes", "description": "Routing table.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_firewall_filter", "description": "Rule firewall filter.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_firewall_nat", "description": "Rule firewall NAT.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_dhcp_server", "description": "DHCP server + leases.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_wireguard", "description": "WireGuard interface + peers.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_queues", "description": "Simple Queue & Queue Tree.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_logs", "description": "Log terbaru.", "parameters": {"type": "object", "properties": {"count": {"type": "integer", "default": 50}}, "required": []}}},
    {"type": "function", "function": {"name": "create_vlan", "description": "Buat VLAN interface.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "vlan_id": {"type": "integer"}, "interface": {"type": "string"}, "comment": {"type": "string"}}, "required": ["name", "vlan_id", "interface"]}}},
    {"type": "function", "function": {"name": "add_ip_address", "description": "Tambah IP address.", "parameters": {"type": "object", "properties": {"address": {"type": "string"}, "interface": {"type": "string"}, "comment": {"type": "string"}}, "required": ["address", "interface"]}}},
    {"type": "function", "function": {"name": "add_firewall_filter", "description": "Tambah rule firewall filter.", "parameters": {"type": "object", "properties": {"chain": {"type": "string", "enum": ["input", "forward", "output"]}, "action": {"type": "string", "enum": ["accept", "drop", "reject"]}, "src_address": {"type": "string"}, "dst_address": {"type": "string"}, "protocol": {"type": "string"}, "dst_port": {"type": "string"}, "comment": {"type": "string"}}, "required": ["chain", "action"]}}},
    {"type": "function", "function": {"name": "add_nat_masquerade", "description": "Tambah NAT masquerade.", "parameters": {"type": "object", "properties": {"out_interface": {"type": "string"}, "src_address": {"type": "string"}, "comment": {"type": "string"}}, "required": ["out_interface"]}}},
    {"type": "function", "function": {"name": "create_dhcp_server", "description": "Buat DHCP server + pool + network.", "parameters": {"type": "object", "properties": {"interface": {"type": "string"}, "address_pool": {"type": "string"}, "network": {"type": "string"}, "gateway": {"type": "string"}, "dns_server": {"type": "string"}, "name": {"type": "string"}}, "required": ["interface", "address_pool", "network", "gateway"]}}},
    {"type": "function", "function": {"name": "create_wireguard_interface", "description": "Buat WireGuard interface.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "listen_port": {"type": "integer"}, "mtu": {"type": "integer"}, "comment": {"type": "string"}}, "required": ["name", "listen_port"]}}},
    {"type": "function", "function": {"name": "add_wireguard_peer", "description": "Tambah WireGuard peer.", "parameters": {"type": "object", "properties": {"interface": {"type": "string"}, "public_key": {"type": "string"}, "allowed_address": {"type": "string"}, "endpoint": {"type": "string"}, "comment": {"type": "string"}}, "required": ["interface", "public_key", "allowed_address"]}}},
    {"type": "function", "function": {"name": "add_simple_queue", "description": "Tambah Simple Queue.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "target": {"type": "string"}, "max_limit": {"type": "string"}, "comment": {"type": "string"}}, "required": ["name", "target", "max_limit"]}}},
    {"type": "function", "function": {"name": "execute_raw", "description": "Eksekusi REST API mentah.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "data": {"type": "object"}}, "required": ["path"]}}},
    
    {"type": "function", "function": {"name": "get_pppoe_active", "description": "Daftar PPPoE active (user online).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_ppp_secret", "description": "Daftar PPP secret (akun PPPoE).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_ppp_profile", "description": "Daftar PPP profile.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_containers", "description": "Daftar container RouterOS dan statusnya.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_scheduler", "description": "Daftar system scheduler.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "get_address_list", "description": "Daftar IP firewall address-list.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "set_ppp_profile", "description": "Ubah profile PPP secret (misal isolir user).", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "profile": {"type": "string"}}, "required": ["name", "profile"]}}},
    {"type": "function", "function": {"name": "remove_pppoe_active", "description": "Putus sesi PPPoE active user.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "container_start", "description": "Start container by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "container_stop", "description": "Stop container by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "remember_fact", "description": "Simpan fakta ke memory jangka panjang. layer=core (singkat, selalu dimuat) atau detail (hanya jika diminta).", "parameters": {"type": "object", "properties": {"fact": {"type": "string"}, "layer": {"type": "string", "enum": ["core", "detail"], "description": "default core"}}, "required": ["fact"]}}},
    {"type": "function", "function": {"name": "show_memory", "description": "Tampilkan memory INTI (yang selalu dimuat).", "parameters": {"type": "object", "properties": {}, "required": []}}},{"type": "function", "function": {"name": "show_memory_detail", "description": "Tampilkan memory DETAIL (topologi lengkap). Panggil hanya jika user butuh detail atau memory inti kurang.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "confirm_action", "description": "Konfirmasi aksi pending.", "parameters": {"type": "object", "properties": {"action_id": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["action_id", "confirm"]}}},
]

WRITE_TOOLS = {"create_vlan", "add_ip_address", "add_firewall_filter", "add_nat_masquerade", "create_dhcp_server", "create_wireguard_interface", "add_wireguard_peer", "add_simple_queue", "execute_raw", "set_ppp_profile", "remove_pppoe_active", "container_start", "container_stop"}



def _allowed_tool_names() -> set:
    names = set()
    for t in TOOLS:
        try:
            names.add(t["function"]["name"])
        except Exception:
            pass
    return names


def _sanitize_tool_name(raw: str) -> str:
    """Perbaiki nama tool rusak dari model (gpt-oss channel tokens, dll)."""
    if not raw:
        return ""
    name = str(raw).strip()
    # buang artefak reasoning gpt-oss / special tokens
    for sep in ("<|channel|>", "<|", "|>", "commentary", "analysis", "final"):
        if sep in name:
            name = name.split(sep)[0]
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    allowed = _allowed_tool_names()
    if name in allowed:
        return name
    # fuzzy: prefix match
    low = name.lower()
    for a in allowed:
        if low == a.lower() or low.startswith(a.lower()) or a.lower().startswith(low):
            return a
    # common aliases
    aliases = {
        "interfaces": "get_interfaces",
        "getinterfaces": "get_interfaces",
        "systemresource": "get_system_resource",
        "resource": "get_system_resource",
        "pppoe": "get_pppoe_active",
        "firewall": "get_firewall_filter",
        "queues": "get_simple_queues",
        "memory": "show_memory",
    }
    for k, v in aliases.items():
        if k in low and v in allowed:
            return v
    return name


def execute_tool(name: str, args: dict, force: bool = False) -> dict:
    if name == "get_system_resource": return mt.get("/system/resource")
    if name == "get_interfaces": return mt.get("/interface")
    if name == "get_ip_addresses": return mt.get("/ip/address")
    if name == "get_routes": return mt.get("/ip/route")
    if name == "get_firewall_filter": return mt.get("/ip/firewall/filter")
    if name == "get_firewall_nat": return mt.get("/ip/firewall/nat")
    if name == "get_dhcp_server":
        return {"servers": mt.get("/ip/dhcp-server"), "networks": mt.get("/ip/dhcp-server/network"), "leases": mt.get("/ip/dhcp-server/lease")}
    if name == "get_wireguard":
        return {"interfaces": mt.get("/interface/wireguard"), "peers": mt.get("/interface/wireguard/peers")}
    if name == "get_queues":
        return {"simple": mt.get("/queue/simple"), "tree": mt.get("/queue/tree")}
    
    if name == "get_pppoe_active":
        return mt.get("/ppp/active")
    if name == "get_ppp_secret":
        return mt.get("/ppp/secret")
    if name == "get_ppp_profile":
        return mt.get("/ppp/profile")
    if name == "get_containers":
        return mt.get("/container")
    if name == "get_scheduler":
        return mt.get("/system/scheduler")
    if name == "get_address_list":
        return mt.get("/ip/firewall/address-list")
    if name == "remember_fact":
        fact = (args.get("fact") or "").strip()
        if not fact:
            return {"error": "fact kosong"}
        layer = (args.get("layer") or "core").lower()
        if layer not in ("core", "detail"):
            layer = "core"
        append_memory(fact, layer=layer)
        return {"ok": True, "saved": fact, "layer": layer}
    if name == "show_memory":
        return {
            "layer": "core",
            "memory": load_memory_layer("core") or "(kosong)",
            "hint": "Panggil show_memory_detail untuk topologi lengkap",
            "storage": STORAGE_BACKEND,
        }
    if name == "show_memory_detail":
        return {
            "layer": "detail",
            "memory": load_memory_layer("detail") or "(kosong)",
            "storage": STORAGE_BACKEND,
        }
    if name == "get_logs":
        logs = mt.get("/log")
        return logs[-args.get("count", 50):] if isinstance(logs, list) else logs
    if name == "confirm_action":
        action_id = args.get("action_id")
        if action_id not in pending_actions:
            return {"error": "Action ID tidak ditemukan"}
        action = pending_actions.pop(action_id)
        if not args.get("confirm"):
            return {"status": "cancelled"}
        return _do_write(action["name"], action["args"])
    # execute_raw GET = read-only, jangan blokir dry-run
    if name == "execute_raw":
        method = (args.get("method") or "GET").upper()
        if method == "GET":
            return mt.get(args.get("path") or "/")
        return _handle_write(name, args, force)
    if name in WRITE_TOOLS:
        return _handle_write(name, args, force)
    return {"error": f"Tool {name} tidak dikenal"}


def _handle_write(name: str, args: dict, force: bool = False) -> dict:
    if DRY_RUN and not force:
        return {"status": "dry-run", "message": f"[DRY-RUN] {name} tidak dijalankan", "would_execute": {"tool": name, "args": args}}
    if REQUIRE_CONFIRMATION and not force:
        action_id = str(uuid.uuid4())[:8]
        pending_actions[action_id] = {"name": name, "args": args}
        return {"status": "pending_confirmation", "action_id": action_id, "message": f"Konfirmasi dengan action_id={action_id}", "preview": args}
    return _do_write(name, args)


def _do_write(name: str, args: dict) -> dict:
    if name == "create_vlan":
        data = {"name": args["name"], "vlan-id": args["vlan_id"], "interface": args["interface"]}
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/interface/vlan", data)
    if name == "add_ip_address":
        data = {"address": args["address"], "interface": args["interface"]}
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/ip/address", data)
    if name == "add_firewall_filter":
        data = {"chain": args.get("chain", "forward"), "action": args.get("action", "drop")}
        for k in ["src_address", "dst_address", "protocol", "dst_port", "comment"]:
            if args.get(k): data[k.replace("_", "-")] = args[k]
        return mt.put("/ip/firewall/filter", data)
    if name == "add_nat_masquerade":
        data = {"chain": "srcnat", "action": "masquerade", "out-interface": args["out_interface"]}
        if args.get("src_address"): data["src-address"] = args["src_address"]
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/ip/firewall/nat", data)
    if name == "create_dhcp_server":
        pool_name = (args.get("name") or "dhcp") + "-pool"
        mt.put("/ip/pool", {"name": pool_name, "ranges": args["address_pool"]})
        net = {"address": args["network"], "gateway": args["gateway"]}
        if args.get("dns_server"): net["dns-server"] = args["dns_server"]
        mt.put("/ip/dhcp-server/network", net)
        return mt.put("/ip/dhcp-server", {"name": args.get("name", "dhcp1"), "interface": args["interface"], "address-pool": pool_name})
    if name == "create_wireguard_interface":
        data = {"name": args["name"], "listen-port": args["listen_port"]}
        if args.get("mtu"): data["mtu"] = args["mtu"]
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/interface/wireguard", data)
    if name == "add_wireguard_peer":
        data = {"interface": args["interface"], "public-key": args["public_key"], "allowed-address": args["allowed_address"]}
        if args.get("endpoint"): data["endpoint"] = args["endpoint"]
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/interface/wireguard/peers", data)
    if name == "add_simple_queue":
        data = {"name": args["name"], "target": args["target"], "max-limit": args["max_limit"]}
        if args.get("comment"): data["comment"] = args["comment"]
        return mt.put("/queue/simple", data)
    
    if name == "set_ppp_profile":
        # find id by name then set profile
        secrets = mt.get("/ppp/secret")
        target = None
        if isinstance(secrets, list):
            for s in secrets:
                if s.get("name") == args["name"]:
                    target = s
                    break
        if not target:
            return {"error": f"PPP secret {args['name']} tidak ditemukan"}
        sid = target.get(".id") or target.get("id")
        return mt.patch(f"/ppp/secret/{sid}", {"profile": args["profile"]})
    if name == "remove_pppoe_active":
        actives = mt.get("/ppp/active")
        target = None
        if isinstance(actives, list):
            for a in actives:
                if a.get("name") == args["name"]:
                    target = a
                    break
        if not target:
            return {"error": f"PPPoE active {args['name']} tidak ditemukan"}
        aid = target.get(".id") or target.get("id")
        return mt.delete(f"/ppp/active/{aid}")
    if name == "container_start":
        containers = mt.get("/container")
        target = None
        if isinstance(containers, list):
            for c in containers:
                if c.get("name") == args["name"] or args["name"] in str(c.get("tag", "")):
                    target = c
                    break
        if not target:
            return {"error": f"Container {args['name']} tidak ditemukan"}
        cid = target.get(".id") or target.get("id")
        return mt.post(f"/container/start", {"number": cid})
    if name == "container_stop":
        containers = mt.get("/container")
        target = None
        if isinstance(containers, list):
            for c in containers:
                if c.get("name") == args["name"] or args["name"] in str(c.get("tag", "")):
                    target = c
                    break
        if not target:
            return {"error": f"Container {args['name']} tidak ditemukan"}
        cid = target.get(".id") or target.get("id")
        return mt.post(f"/container/stop", {"number": cid})
    if name == "execute_raw":
        method = args.get("method", "GET").upper()
        if method == "GET": return mt.get(args["path"])
        if method == "POST": return mt.post(args["path"], args.get("data") or {})
        if method == "PUT": return mt.put(args["path"], args.get("data") or {})
        if method == "PATCH": return mt.patch(args["path"], args.get("data") or {})
        if method == "DELETE": return mt.delete(args["path"])
    return {"error": f"{name} belum diimplementasi"}




# ─── SQLite storage ─────────────────────────────────────────────
_db_conn = None


def _db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    ensure_data_dir()
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # migrate legacy name oserv_agent.db → data_agent.db (once)
    try:
        legacy = SQLITE_PATH.parent / "oserv_agent.db"
        if legacy.exists() and not SQLITE_PATH.exists():
            legacy.rename(SQLITE_PATH)
            for suf in ("-wal", "-shm"):
                leg = Path(str(legacy) + suf)
                if leg.exists():
                    leg.rename(Path(str(SQLITE_PATH) + suf))
    except Exception:
        pass
    _db_conn = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
    _db_conn.row_factory = sqlite3.Row
    _db_conn.execute("PRAGMA journal_mode=WAL;")
    _db_conn.execute("PRAGMA synchronous=NORMAL;")
    _db_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            messages_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS session_meta (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT OR IGNORE INTO memory (id, content, updated_at) VALUES (1, '', datetime('now'));
        CREATE TABLE IF NOT EXISTS memory_kv (
            key TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        );
        INSERT OR IGNORE INTO memory_kv (key, content, updated_at) VALUES ('core', '', datetime('now'));
        INSERT OR IGNORE INTO memory_kv (key, content, updated_at) VALUES ('detail', '', datetime('now'));
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_call_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(session_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_sid_seq ON messages(session_id, seq);
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        );
        """
    )
    _db_conn.commit()
    try:
        cols = {r[1] for r in _db_conn.execute("PRAGMA table_info(session_meta)").fetchall()}
        if "created_at" not in cols:
            _db_conn.execute("ALTER TABLE session_meta ADD COLUMN created_at TEXT")
            _db_conn.execute("UPDATE session_meta SET created_at=updated_at WHERE created_at IS NULL OR created_at=''")
            _db_conn.commit()
    except Exception as _me:
        console.print(f"[yellow]session_meta migrate: {_me}[/yellow]")
    _ensure_messages_from_sessions_json()
    try:
        cols = [r[1] for r in _db_conn.execute("PRAGMA table_info(session_meta)").fetchall()]
        if "provider" not in cols:
            _db_conn.execute("ALTER TABLE session_meta ADD COLUMN provider TEXT DEFAULT 'openrouter'")
            _db_conn.commit()
    except Exception as _pe:
        console.print(f"[yellow]provider col: {_pe}[/yellow]")
    try:
        _db_conn.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT
            )"""
        )
        _db_conn.commit()
    except Exception as _se:
        console.print(f"[yellow]settings table: {_se}[/yellow]")
    _load_settings_from_db()
    _migrate_files_to_sqlite_once()
    return _db_conn


def _table_exists(name: str) -> bool:
    try:
        r = _db_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return r is not None
    except Exception:
        return False


def _ensure_messages_from_sessions_json() -> None:
    """Backfill tabel messages dari sessions.messages_json jika masih kosong."""
    import json as _json
    try:
        if not _table_exists("messages"):
            _db_conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(session_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_sid_seq ON messages(session_id, seq);
                """
            )
            _db_conn.commit()
        n = _db_conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        if n > 0:
            return
        rows = _db_conn.execute("SELECT session_id, messages_json FROM sessions").fetchall()
        filled = 0
        for row in rows:
            sid = row["session_id"]
            try:
                hist = _json.loads(row["messages_json"] or "[]")
            except Exception:
                continue
            if not isinstance(hist, list):
                continue
            seq = 0
            for msg in hist:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or "assistant"
                content = msg.get("content")
                if content is None:
                    content = ""
                tc = msg.get("tool_calls")
                tc_s = _json.dumps(tc, ensure_ascii=False) if tc else None
                _db_conn.execute(
                    "INSERT OR IGNORE INTO messages(session_id, seq, role, content, tool_calls, tool_call_id) VALUES (?,?,?,?,?,?)",
                    (sid, seq, role, str(content), tc_s, msg.get("tool_call_id")),
                )
                seq += 1
            if seq:
                filled += 1
        if filled:
            _db_conn.commit()
            console.print(f"[green]Backfill messages dari sessions.json blob: {filled} sesi[/green]")
    except Exception as e:
        console.print(f"[yellow]ensure messages: {e}[/yellow]")


def _migrate_files_to_sqlite_once() -> None:
    """Impor memory.md / sessions.json jika DB masih kosong."""
    try:
        row = _db_conn.execute("SELECT content FROM memory WHERE id=1").fetchone()
        content = (row["content"] if row else "") or ""
        if not content.strip() and MEMORY_FILE.exists():
            text = MEMORY_FILE.read_text(encoding="utf-8").strip()
            if text:
                _db_conn.execute(
                    "UPDATE memory SET content=?, updated_at=datetime('now') WHERE id=1",
                    (text,),
                )
                _db_conn.commit()
                console.print("[green]Migrasi memory.md → SQLite[/green]")
        # core / detail files
        for key, fpath in (("core", MEMORY_CORE_FILE), ("detail", MEMORY_DETAIL_FILE)):
            try:
                rowk = _db_conn.execute("SELECT content FROM memory_kv WHERE key=?", (key,)).fetchone()
                cur = ((rowk["content"] if rowk else "") or "").strip()
                if not cur and fpath.exists():
                    t = fpath.read_text(encoding="utf-8").strip()
                    if t:
                        _db_conn.execute(
                            "INSERT OR REPLACE INTO memory_kv(key, content, updated_at) VALUES (?,?,datetime('now'))",
                            (key, t),
                        )
                        _db_conn.commit()
                        console.print(f"[green]Migrasi {fpath.name} → SQLite memory_kv[{key}][/green]")
            except Exception as me:
                console.print(f"[yellow]memory_kv migrate {key}: {me}[/yellow]")
        n = _db_conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        if n == 0 and SESSIONS_FILE.exists():
            import json as _json
            data = _json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for sid, hist in data.items():
                    _db_conn.execute(
                        "INSERT OR REPLACE INTO sessions(session_id, messages_json, updated_at) VALUES (?,?,datetime('now'))",
                        (sid, _json.dumps(hist, ensure_ascii=False)),
                    )
                _db_conn.commit()
                console.print(f"[green]Migrasi sessions.json → SQLite ({len(data)} sesi)[/green]")
        n2 = _db_conn.execute("SELECT COUNT(*) AS c FROM session_meta").fetchone()["c"]
        if n2 == 0 and SESSION_META_FILE.exists():
            import json as _json
            meta = _json.loads(SESSION_META_FILE.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                for sid, item in meta.items():
                    _db_conn.execute(
                        "INSERT OR REPLACE INTO session_meta(session_id, title, updated_at) VALUES (?,?,?)",
                        (sid, (item or {}).get("title") or f"Sesi {sid[:8]}", (item or {}).get("updated_at") or ""),
                    )
                _db_conn.commit()
                console.print("[green]Migrasi session_meta.json → SQLite[/green]")
    except Exception as e:
        console.print(f"[yellow]Migrasi SQLite skip: {e}[/yellow]")


def ensure_data_dir() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        console.print(f"[yellow]WARN: tidak bisa buat DATA_DIR {DATA_DIR}: {e}[/yellow]")



def _read_file(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def load_memory_layer(layer: str = "core") -> str:
    """layer: core | detail | all(legacy single)"""
    layer = (layer or "core").lower()
    try:
        if STORAGE_BACKEND == "sqlite":
            if layer in ("core", "detail"):
                row = _db().execute("SELECT content FROM memory_kv WHERE key=?", (layer,)).fetchone()
                text = ((row["content"] if row else "") or "").strip()
                if text:
                    return text
                # fallback file
                return _read_file(MEMORY_CORE_FILE if layer == "core" else MEMORY_DETAIL_FILE)
            # legacy full
            row = _db().execute("SELECT content FROM memory WHERE id=1").fetchone()
            return ((row["content"] if row else "") or "").strip() or _read_file(MEMORY_FILE)
        if layer == "core":
            return _read_file(MEMORY_CORE_FILE) or _read_file(MEMORY_FILE)
        if layer == "detail":
            return _read_file(MEMORY_DETAIL_FILE)
        return _read_file(MEMORY_FILE)
    except Exception as e:
        console.print(f"[yellow]WARN: load memory {layer}: {e}[/yellow]")
        return ""


def load_memory() -> str:
    """Kompatibel: mengembalikan memory INTI (hemat context)."""
    return load_memory_layer("core")


def save_memory_layer(layer: str, text: str) -> None:
    try:
        ensure_data_dir()
        body = (text or "").strip()
        layer = (layer or "core").lower()
        if STORAGE_BACKEND == "sqlite":
            if layer in ("core", "detail"):
                _db().execute(
                    "INSERT OR REPLACE INTO memory_kv(key, content, updated_at) VALUES (?,?,datetime('now'))",
                    (layer, body),
                )
                _db().commit()
                # Memory hanya SQLite (tanpa file .md)
                return
            _db().execute(
                "UPDATE memory SET content=?, updated_at=datetime('now') WHERE id=1",
                (body,),
            )
            _db().commit()
        # non-sqlite fallback only
        fpath = MEMORY_CORE_FILE if layer == "core" else (MEMORY_DETAIL_FILE if layer == "detail" else MEMORY_FILE)
        fpath.write_text(body + "\n", encoding="utf-8")
    except Exception as e:
        console.print(f"[yellow]WARN: save memory {layer}: {e}[/yellow]")


def save_memory(text: str) -> None:
    """Default simpan ke layer core."""
    save_memory_layer("core", text)


def append_memory(note: str, layer: str = "core") -> None:
    note = (note or "").strip()
    if not note:
        return
    prev = load_memory_layer(layer)
    line = f"- {note}" if not note.startswith("-") else note
    merged = (prev + "\n" + line).strip() if prev else line
    max_c = MEMORY_MAX_CHARS if layer == "detail" else min(MEMORY_MAX_CHARS, 4000)
    if len(merged) > max_c:
        merged = merged[-max_c:]
    save_memory_layer(layer, merged)


def seed_default_memory() -> None:
    if not load_memory_layer("core"):
        # seed from files if present
        for layer, f in (("core", MEMORY_CORE_FILE), ("detail", MEMORY_DETAIL_FILE)):
            t = _read_file(f)
            if t:
                save_memory_layer(layer, t)
        if not load_memory_layer("core"):
            save_memory_layer(
                "core",
                "- Identity router belum diisi. Admin dapat menyimpan fakta via remember_fact.",
            )


def build_system_prompt(lang: str = "en") -> str:
    lang = (lang or "en").lower().strip()
    if lang not in ("en", "id"):
        lang = "en"
    mem = load_memory_layer("core")
    mem_block = ""
    if mem:
        if lang == "id":
            mem_block = (
                "\n\n## Long-term Memory INTI (selalu aktif, hemat token)\n"
                "Gunakan fakta inti di bawah. Untuk detail topologi/user/service panggil tool show_memory_detail.\n"
                f"{mem}\n"
            )
        else:
            mem_block = (
                "\n\n## Core long-term memory (always on, token-efficient)\n"
                "Use the facts below. For topology/user/service detail call show_memory_detail.\n"
                f"{mem}\n"
            )
    mode = (
        f"DRY_RUN={DRY_RUN} (if true: writes are simulation only). "
        f"REQUIRE_CONFIRMATION={REQUIRE_CONFIRMATION} (if true: writes need confirm_action)."
    )
    if lang == "id":
        base = f"""Kamu adalah asisten jaringan ahli MikroTik RouterOS untuk ISP (Agent).
Kamu BERJALAN DI CONTAINER AGENT USER yang terhubung ke router MikroTik nyata via REST API.
Bukan platform AI terpisah. Bukan sandbox hardcoded. Tool memanggil router user secara langsung.

Status keamanan saat ini (wajib diikuti, diperbarui tiap request):
{mode}

Aturan:
1. Gunakan tool yang tersedia. Jangan mengarang data router.
2. Jika DRY_RUN=False dan REQUIRE_CONFIRMATION=False → eksekusi write/tool langsung (hasil nyata dari router).
3. Jika DRY_RUN=True → jangan bilang "hardcoded platform"; bilang mode dry-run aktif, tampilkan would_execute.
4. Jika REQUIRE_CONFIRMATION=True dan ada pending_confirmation → beritahu action_id agar user konfirmasi.
5. GET/read selalu dijalankan nyata, tidak diblokir dry-run.
6. Jangan bilang kamu tidak bisa mengeksekusi karena batasan platform AI.
7. Jawab dalam Bahasa Indonesia.
8. Jawaban SINGKAT dan to the point.
9. Prefer bullet/tabel ringkas.
"""
    else:
        base = f"""You are an expert MikroTik RouterOS network assistant for ISP operations (Agent).
You RUN INSIDE the user's agent container connected to a real MikroTik router via REST API.
You are not a separate AI platform or hardcoded sandbox. Tools call the user's router directly.

Current safety status (must follow; refreshed every request):
{mode}

Rules:
1. Use available tools. Do not invent router data.
2. If DRY_RUN=False and REQUIRE_CONFIRMATION=False → execute write tools immediately (real router results).
3. If DRY_RUN=True → do not say "hardcoded platform"; state dry-run is active and show would_execute.
4. If REQUIRE_CONFIRMATION=True and there is pending_confirmation → tell the user the action_id to confirm.
5. GET/read always runs for real; not blocked by dry-run.
6. Never claim you cannot execute due to AI platform limits.
7. Reply in English (US/international technical English).
8. Keep answers SHORT and to the point.
9. Prefer concise bullets/tables.
"""
    return base + mem_block





def _load_settings_from_db() -> None:
    global LLM_PROVIDER, OPENROUTER_API_KEY, OPENROUTER_MODEL, GROQ_API_KEY, GROQ_MODEL
    global MIKROTIK_HOST, MIKROTIK_USER, MIKROTIK_PASS, MIKROTIK_PORT, API_TOKEN, client
    try:
        if STORAGE_BACKEND != "sqlite" or _db_conn is None:
            return
        rows = _db_conn.execute("SELECT key, value FROM settings").fetchall()
        kv = {r["key"]: r["value"] for r in rows}
        if kv.get("llm_provider") in ("openrouter", "groq"):
            LLM_PROVIDER = kv["llm_provider"]
        if kv.get("openrouter_api_key"):
            OPENROUTER_API_KEY = kv["openrouter_api_key"]
        if kv.get("openrouter_model"):
            OPENROUTER_MODEL = kv["openrouter_model"]
        if kv.get("groq_api_key"):
            GROQ_API_KEY = kv["groq_api_key"]
        if kv.get("groq_model"):
            GROQ_MODEL = kv["groq_model"]
        if "mikrotik_host" in kv:
            MIKROTIK_HOST = kv.get("mikrotik_host") or ""
        if "mikrotik_user" in kv:
            MIKROTIK_USER = kv.get("mikrotik_user") or ""
        if "mikrotik_pass" in kv:
            MIKROTIK_PASS = kv.get("mikrotik_pass") or ""
        if kv.get("mikrotik_port"):
            MIKROTIK_PORT = kv.get("mikrotik_port") or "443"
        if "api_token" in kv:
            API_TOKEN = kv.get("api_token") or ""
        rebuild_client()
        try:
            mt.reconfigure()
        except Exception:
            pass
    except Exception:
        pass



def _save_setting(key: str, value: str) -> None:
    try:
        if STORAGE_BACKEND != "sqlite":
            return
        db = _db()
        db.execute(
            "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?,?,datetime('now'))",
            (key, value or ""),
        )
        db.commit()
    except Exception as e:
        console.print(f"[yellow]settings save: {e}[/yellow]")


def _mask_key(k: str) -> str:
    k = (k or "").strip()
    if len(k) < 8:
        return "••••" if k else ""
    return k[:4] + "••••" + k[-4:]


def load_session_meta() -> dict:
    try:
        if STORAGE_BACKEND == "sqlite":
            try:
                rows = _db().execute(
                    "SELECT session_id, title, created_at, updated_at, provider FROM session_meta"
                ).fetchall()
            except Exception:
                rows = _db().execute(
                    "SELECT session_id, title, created_at, updated_at FROM session_meta"
                ).fetchall()
            out = {}
            for r in rows:
                keys = r.keys() if hasattr(r, "keys") else []
                out[r["session_id"]] = {
                    "title": r["title"] or f"Sesi {r['session_id'][:8]}",
                    "created_at": r["created_at"] or r["updated_at"] or "",
                    "updated_at": r["updated_at"] or r["created_at"] or "",
                    "provider": (r["provider"] if "provider" in keys else "openrouter") or "openrouter",
                }
            return out
        if SESSION_META_FILE.exists():
            import json as _json
            data = _json.loads(SESSION_META_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_session_meta(meta: dict) -> None:
    try:
        ensure_data_dir()
        if STORAGE_BACKEND == "sqlite":
            db = _db()
            db.execute("DELETE FROM session_meta")
            for sid, item in (meta or {}).items():
                try:
                    db.execute(
                        "INSERT OR REPLACE INTO session_meta(session_id, title, created_at, updated_at, provider) VALUES (?,?,?,?,?)",
                        (
                            sid,
                            (item or {}).get("title") or f"Sesi {str(sid)[:8]}",
                            (item or {}).get("created_at") or (item or {}).get("updated_at") or "",
                            (item or {}).get("updated_at") or (item or {}).get("created_at") or "",
                            (item or {}).get("provider") or "openrouter",
                        ),
                    )
                except Exception:
                    db.execute(
                        "INSERT OR REPLACE INTO session_meta(session_id, title, created_at, updated_at) VALUES (?,?,?,?)",
                        (
                            sid,
                            (item or {}).get("title") or f"Sesi {str(sid)[:8]}",
                            (item or {}).get("created_at") or (item or {}).get("updated_at") or "",
                            (item or {}).get("updated_at") or (item or {}).get("created_at") or "",
                        ),
                    )
            db.commit()
            return
        import json as _json
        SESSION_META_FILE.write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        console.print(f"[yellow]WARN: gagal save session meta: {e}[/yellow]")


def touch_session_meta(sid: str, title: str | None = None, provider: str | None = None) -> None:
    meta = load_session_meta()
    item = meta.get(sid) or {}
    if title:
        item["title"] = title.strip()[:80]
    if "title" not in item:
        item["title"] = f"Sesi {sid[:8]}"
    if not item.get("created_at"):
        item["created_at"] = now_wib_iso()
        item["provider"] = provider or LLM_PROVIDER
    if not item.get("provider"):
        item["provider"] = provider or LLM_PROVIDER
    item["updated_at"] = now_wib_iso()
    meta[sid] = item
    save_session_meta(meta)

def load_sessions() -> dict:
    if not PERSIST_SESSIONS:
        return {}
    try:
        if STORAGE_BACKEND == "sqlite":
            import json as _json
            rows = _db().execute("SELECT session_id, messages_json FROM sessions").fetchall()
            out = {}
            for r in rows:
                try:
                    hist = _json.loads(r["messages_json"] or "[]")
                    if isinstance(hist, list):
                        out[r["session_id"]] = hist
                except Exception:
                    continue
            return out
        if SESSIONS_FILE.exists():
            import json as _json
            data = _json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        console.print(f"[yellow]WARN: gagal load sessions: {e}[/yellow]")
    return {}


def save_sessions(store: dict) -> None:
    if not PERSIST_SESSIONS:
        return
    try:
        ensure_data_dir()
        import json as _json
        clean = {}
        for sid, hist in (store or {}).items():
            out = []
            for m in hist or []:
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                content = m.get("content")
                if role not in ("system", "user", "assistant", "tool"):
                    continue
                item = {"role": role, "content": content if content is not None else ""}
                if m.get("created_at"):
                    item["created_at"] = m["created_at"]
                if role == "assistant" and m.get("tool_calls"):
                    item["tool_calls"] = m["tool_calls"]
                if role == "tool" and m.get("tool_call_id"):
                    item["tool_call_id"] = m["tool_call_id"]
                # simpan assistant kosong hanya jika ada tool_calls
                if role == "assistant" and not str(item.get("content") or "").strip() and not item.get("tool_calls"):
                    continue
                out.append(item)
            clean[sid] = out
        if STORAGE_BACKEND == "sqlite":
            db = _db()
            keep = set(clean.keys())
            # hapus sesi yang sudah tidak ada di memori (rename tetap, delete hilang)
            try:
                existing = [r["session_id"] for r in db.execute("SELECT session_id FROM sessions").fetchall()]
                for old_sid in existing:
                    if old_sid not in keep:
                        db.execute("DELETE FROM sessions WHERE session_id=?", (old_sid,))
                        db.execute("DELETE FROM messages WHERE session_id=?", (old_sid,))
            except Exception as de:
                console.print(f"[yellow]cleanup sessions: {de}[/yellow]")
            for sid, hist in clean.items():
                db.execute(
                    "INSERT OR REPLACE INTO sessions(session_id, messages_json, updated_at) VALUES (?,?,datetime('now'))",
                    (sid, _json.dumps(hist, ensure_ascii=False)),
                )
                db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                seq = 0
                for msg in hist:
                    role = msg.get("role") or "assistant"
                    content = msg.get("content")
                    if content is None:
                        content = ""
                    tc = msg.get("tool_calls")
                    tc_s = _json.dumps(tc, ensure_ascii=False) if tc else None
                    ca = msg.get("created_at") or ""
                    try:
                        db.execute(
                            "INSERT INTO messages(session_id, seq, role, content, tool_calls, tool_call_id, created_at) VALUES (?,?,?,?,?,?,?)",
                            (sid, seq, role, str(content), tc_s, msg.get("tool_call_id"), ca or None),
                        )
                    except Exception:
                        db.execute(
                            "INSERT INTO messages(session_id, seq, role, content, tool_calls, tool_call_id) VALUES (?,?,?,?,?,?)",
                            (sid, seq, role, str(content), tc_s, msg.get("tool_call_id")),
                        )
                    seq += 1
            db.commit()
            return
        SESSIONS_FILE.write_text(_json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        console.print(f"[yellow]WARN: gagal save sessions: {e}[/yellow]")






# Batasi history agar tidak meledak context / Internal Server Error
# API_HISTORY_WINDOW / WARN defined above (API context only)
# Jika true: ringkas pesan lama jadi 1 summary (sesi lebih panjang tanpa error)



def _trim_history(history: List[dict], limit: int | None = None) -> List[dict]:
    """Window ke API saja — tidak menghapus history tersimpan di caller."""
    if not history:
        return history
    lim = max(4, int(limit if limit is not None else API_HISTORY_WINDOW))
    sys = history[0] if history[0].get("role") == "system" else None
    rest = history[1:] if sys else list(history)
    if len(rest) <= lim:
        return list(history)
    rest = rest[-lim:]
    while rest:
        m0 = rest[0]
        if m0.get("role") == "tool":
            rest.pop(0)
            continue
        if m0.get("role") == "assistant" and m0.get("tool_calls"):
            rest.pop(0)
            while rest and rest[0].get("role") == "tool":
                rest.pop(0)
            continue
        break
    return ([sys] if sys else []) + rest


def _messages_for_api(history: List[dict]) -> List[dict]:
    """Sanitize for OpenAI-compatible API."""
    out = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or "",
                "content": m.get("content") or "",
            })
        elif role == "assistant":
            item = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                item["tool_calls"] = m["tool_calls"]
            out.append(item)
        elif role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def run_conversation(user_message: str, history: List[dict] = None, lang: str = "en"):
    lang = (lang or "en").lower().strip()
    if lang not in ("en", "id"):
        lang = "en"
    if history is None:
        history = [{"role": "system", "content": build_system_prompt(lang)}]
    else:
        # Refresh system prompt each request so DRY_RUN/CONFIRM and language apply
        sys_msg = {"role": "system", "content": build_system_prompt(lang)}
        if history and history[0].get("role") == "system":
            history[0] = sys_msg
        else:
            history.insert(0, sys_msg)
    history.append({"role": "user", "content": user_message, "created_at": now_wib_iso()})
    # History penuh tetap di SQLite/memori. API hanya menerima window terakhir.
    api_window = _api_window_default()

    for attempt in range(10):
        api_messages = _messages_for_api(_trim_history(list(history), limit=api_window))
        try:
            response = client.chat.completions.create(
                model=_active_model(),
                messages=api_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.3,
            )
        except Exception as e:
            err = str(e)
            console.print(f"[red]LLM API error: {err}[/red]")
            low = err.lower()
            if "rate" in low or "429" in low:
                return (
                    "⚠️ **Rate limit** — kuota free habis atau terlalu sering request. "
                    "Tunggu sebentar atau ganti model di dropdown / pengaturan.",
                    history,
                )
            # Context overflow: jangan hapus history tersimpan; coba ulang dengan window lebih kecil
            if any(x in low for x in (
                "context_length", "context length", "maximum context",
                "too many tokens", "prompt is too long",
                "exceeds the model", "exceed context", "string_above_max_length",
                "request too large", "413", "tokens per minute", "tpm",
                "reduce your message size",
            )):
                api_window = max(4, api_window // 2)
                console.print(f"[yellow]Payload/TPM terlalu besar → retry window={api_window}[/yellow]")
                if attempt >= 9:
                    return (
                        "⚠️ Request ke model terlalu besar (batas TPM/context free tier). "
                        "History chat tetap aman. Coba: pertanyaan lebih singkat, model lebih kecil "
                        "(gpt-oss-20b), atau kurangi memory detail.",
                        history,
                    )
                continue
            if "decommissioned" in low or "does not exist" in low or "not found" in low:
                return (
                    f"❌ Model tidak tersedia: `{_active_model()}`. "
                    "Buka Pengaturan / dropdown engine, pilih model free yang masih aktif "
                    "(mis. openai/gpt-oss-20b atau qwen/qwen3.8-27b).",
                    history,
                )
            if "tool calling" in low or "tool_choice" in low or "tools are not supported" in low:
                return (
                    f"❌ Model `{_active_model()}` tidak mendukung tool calling (wajib untuk agent MikroTik). "
                    "Pilih model: qwen/qwen3.8-27b atau openai/gpt-oss-20b.",
                    history,
                )
            if "tool call validation" in low or "not in request.tools" in low:
                # Model kirim nama tool rusak — buang asisten tool_calls terakhir & minta jawaban teks
                while history and history[-1].get("role") in ("tool", "assistant"):
                    last = history[-1]
                    if last.get("role") == "assistant" and last.get("tool_calls"):
                        history.pop()
                        break
                    if last.get("role") == "tool":
                        history.pop()
                        continue
                    break
                api_window = max(4, api_window // 2)
                if attempt >= 8:
                    return (
                        "⚠️ Model mengirim tool call tidak valid. "
                        "Coba model **qwen/qwen3.8-27b** (lebih stabil untuk tools) atau ulangi prompt.",
                        history,
                    )
                continue
            return (f"❌ Gagal memanggil model: `{err[:300]}`", history)

        msg = response.choices[0].message
        asst = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            asst["tool_calls"] = []
            for tc in msg.tool_calls:
                clean_name = _sanitize_tool_name(tc.function.name or "")
                asst["tool_calls"].append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": clean_name or (tc.function.name or "unknown"),
                        "arguments": tc.function.arguments or "{}",
                    },
                })
        if not asst.get("created_at"):
            asst["created_at"] = now_wib_iso()
        history.append(asst)
        if not msg.tool_calls:
            text = msg.content or "(kosong)"
            return text, history

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            raw_name = tc.function.name or ""
            name = _sanitize_tool_name(raw_name)
            if name != raw_name:
                console.print(f"[yellow]tool name fixed: {raw_name!r} → {name!r}[/yellow]")
            if name not in _allowed_tool_names():
                result = {
                    "error": f"tool tidak dikenal: {raw_name}",
                    "hint": "Gunakan tool resmi agent (get_system_resource, get_interfaces, …)",
                }
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                    "created_at": now_wib_iso(),
                })
                continue
            console.print(f"[dim]→ {name}({json.dumps(args, ensure_ascii=False)})[/dim]")
            try:
                result = execute_tool(name, args)
            except Exception as te:
                result = {"error": str(te)}
            # batasi ukuran hasil tool agar history tidak membengkak
            raw = json.dumps(result, ensure_ascii=False, default=str)
            if len(raw) > 8000:
                raw = raw[:8000] + "...(truncated)"
            history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": raw,
                "created_at": now_wib_iso(),
            })
        # jangan trim history tersimpan — hanya API yang di-window
    return "Terlalu banyak langkah tool dalam satu putaran. Coba pecah permintaan menjadi lebih spesifik.", history


def run_cli():
    console.print(Panel.fit(
        f"[bold green]MikroTik × OpenRouter Agent[/bold green]\n"
        f"Model : {_active_model()} ({LLM_PROVIDER})\n"
        f"Router: {MIKROTIK_HOST}\n"
        f"DRY_RUN: {DRY_RUN} | CONFIRM: {REQUIRE_CONFIRMATION}",
        title="Ready"
    ))
    history = [{"role": "system", "content": build_system_prompt(lang)}]
    while True:
        try:
            inp = console.input("[bold cyan]Anda › [/bold cyan]").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not inp or inp.lower() in ("exit", "quit", "keluar", "q"):
            break
        with console.status("[green]Berpikir...[/green]"):
            answer, history = run_conversation(inp, history, lang="en")
        console.print(Markdown(answer))
        console.print()


from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="MikroTik OpenRouter Agent")
security = HTTPBearer(auto_error=False)
sessions: Dict[str, List] = load_sessions()

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = "en"


@app.get("/")
def ui():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"message": "UI belum tersedia. Gunakan POST /chat"}
    html = index.read_text(encoding="utf-8")
    token_js = json.dumps(API_TOKEN or "")
    inject = (
        "<script>(function(){try{if(typeof crypto!=='undefined'&&typeof crypto.randomUUID!=='function')"
        "{crypto.randomUUID=function(){return'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,function(c)"
        "{var r=Math.random()*16|0,v=c==='x'?r:(r&0x3|0x8);return v.toString(16)})}}}catch(e){}})();</script>\n"
        f"<script>window.__API_TOKEN__={token_js};"
        f"try{{if(window.__API_TOKEN__)localStorage.setItem('api_token',window.__API_TOKEN__);}}catch(e){{}}</script>"
    )
    if "</head>" in html:
        html = html.replace("</head>", inject + "\n</head>", 1)
    elif "<body" in html:
        html = re.sub(r"(<body[^>]*>)", r"\1" + inject, html, count=1)
    else:
        html = inject + html
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)



@app.get("/favicon.svg")
def favicon():
    path = STATIC_DIR / "favicon.svg"
    if path.exists():
        return FileResponse(path, media_type="image/svg+xml")
    raise HTTPException(404, "favicon not found")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": "openrouter",
        "model": _active_model(),
        "provider": LLM_PROVIDER,
        "dry_run": DRY_RUN,
        "require_confirmation": REQUIRE_CONFIRMATION,
        "router": MIKROTIK_HOST,
    }



class SafetyUpdate(BaseModel):
    dry_run: Optional[bool] = None
    require_confirmation: Optional[bool] = None


@app.get("/safety")
def get_safety():
    return {
        "dry_run": DRY_RUN,
        "require_confirmation": REQUIRE_CONFIRMATION,
        "model": _active_model(),
        "provider": LLM_PROVIDER,
        "router": MIKROTIK_HOST,
        "status": "ok",
    }


@app.post("/safety")
def set_safety(body: SafetyUpdate, cred: HTTPAuthorizationCredentials = Depends(security)):
    """Toggle DRY_RUN / REQUIRE_CONFIRMATION at runtime (no restart)."""
    global DRY_RUN, REQUIRE_CONFIRMATION
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    if body.dry_run is not None:
        DRY_RUN = bool(body.dry_run)
    if body.require_confirmation is not None:
        REQUIRE_CONFIRMATION = bool(body.require_confirmation)
    return {
        "ok": True,
        "dry_run": DRY_RUN,
        "require_confirmation": REQUIRE_CONFIRMATION,
    }



@app.get("/models")
def list_models_http():
    key_ok = bool(_active_api_key())
    if not key_ok:
        return {
            "current": "",
            "provider": LLM_PROVIDER,
            "models": [],
            "key_required": True,
            "message": "API Key Required",
        }
    items = _list_models_for_provider(LLM_PROVIDER)
    cur = _active_model()
    if not (cur or "").strip():
        return {
            "current": "",
            "provider": LLM_PROVIDER,
            "models": items,
            "key_required": False,
            "message": "API Key Required",
        }
    if cur and cur not in {x["id"] for x in items}:
        items.insert(0, {"id": cur, "label": cur.split("/")[-1], "context": "?", "provider": LLM_PROVIDER})
    return {
        "current": cur,
        "provider": LLM_PROVIDER,
        "models": items,
        "key_required": False,
        "message": "",
    }


class ModelUpdate(BaseModel):
    model: str



class ProviderUpdate(BaseModel):
    ui_language: Optional[str] = None
    provider: str = Field(..., description="openrouter | groq")
    openrouter_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None
    groq_model: Optional[str] = None
    mikrotik_host: Optional[str] = None
    mikrotik_user: Optional[str] = None
    mikrotik_pass: Optional[str] = None
    mikrotik_port: Optional[str] = None
    api_token: Optional[str] = None



class KeyCheck(BaseModel):
    provider: str
    api_key: str


@app.post("/provider/validate")
def validate_provider_key(body: KeyCheck):
    """Cek key tanpa menyimpan. Return status: active | invalid | error."""
    prov = (body.provider or "").lower().strip()
    key = (body.api_key or "").strip()
    if prov not in ("openrouter", "groq"):
        return {"ok": False, "status": "invalid", "message": "Provider tidak dikenal"}
    if not key:
        return {"ok": False, "status": "invalid", "message": "API key kosong"}
    try:
        if prov == "groq":
            r = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=12,
            )
        else:
            r = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=12,
            )
        if r.status_code in (401, 403):
            return {"ok": False, "status": "invalid", "message": "Key invalid / ditolak (401/403)"}
        if r.status_code == 402:
            return {"ok": False, "status": "expired", "message": "Key expired / kuota habis (402)"}
        if r.status_code == 429:
            return {"ok": True, "status": "active", "message": "Key valid (rate limit sementara)"}
        if r.ok:
            return {"ok": True, "status": "active", "message": "Key Valid : Active"}
        return {
            "ok": False,
            "status": "error",
            "message": f"HTTP {r.status_code}: {(r.text or '')[:120]}",
        }
    except Exception as e:
        return {"ok": False, "status": "error", "message": str(e)[:160]}


@app.get("/provider")

def _load_ui_language() -> str:
    try:
        if _db_conn is None:
            return "en"
        row = _db_conn.execute("SELECT value FROM settings WHERE key=?", ("ui_language",)).fetchone()
        if row and str(row["value"] or "").strip() in ("en", "id"):
            return str(row["value"]).strip()
    except Exception:
        pass
    return "en"



@app.post("/ui-language")
def set_ui_language_public(body: dict):
    """Persist UI language (en|id). No auth — preference only, no secrets."""
    lang = str((body or {}).get("language") or (body or {}).get("ui_language") or "en").strip().lower()
    if lang not in ("en", "id"):
        lang = "en"
    _save_setting("ui_language", lang)
    return {"ok": True, "ui_language": lang}


@app.get("/ui-language")
def get_ui_language_public():
    return {"ui_language": _load_ui_language()}


def get_provider():
    return {
        "provider": LLM_PROVIDER,
        "openrouter_api_key_set": bool((OPENROUTER_API_KEY or "").strip()),
        "groq_api_key_set": bool((GROQ_API_KEY or "").strip()),
        "openrouter_api_key_mask": _mask_key(OPENROUTER_API_KEY or ""),
        "groq_api_key_mask": _mask_key(GROQ_API_KEY or ""),
        "openrouter_model": OPENROUTER_MODEL,
        "groq_model": GROQ_MODEL,
        "model": _active_model(),
        "base_url": _provider_base_url(),
        "ui_language": (_load_ui_language()),
        "mikrotik_host": MIKROTIK_HOST or "",
        "mikrotik_user": MIKROTIK_USER or "",
        "mikrotik_pass_set": bool((MIKROTIK_PASS or "").strip()),
        "mikrotik_pass_mask": _mask_key(MIKROTIK_PASS or ""),
        "mikrotik_port": MIKROTIK_PORT or "443",
        "api_token_set": bool((API_TOKEN or "").strip()),
        "api_token_mask": _mask_key(API_TOKEN or ""),
    }



@app.post("/provider")
def set_provider(body: ProviderUpdate, cred: HTTPAuthorizationCredentials = Depends(security)):
    global LLM_PROVIDER, OPENROUTER_API_KEY, GROQ_API_KEY, OPENROUTER_MODEL, GROQ_MODEL
    global MIKROTIK_HOST, MIKROTIK_USER, MIKROTIK_PASS, MIKROTIK_PORT, API_TOKEN
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    # Allow language-only updates without forcing provider
    if body.ui_language is not None:
        lang = str(body.ui_language).strip().lower()
        if lang not in ("en", "id"):
            lang = "en"
        _save_setting("ui_language", lang)

    prov = (body.provider or LLM_PROVIDER or "openrouter").lower().strip()
    if body.provider is not None and prov not in ("openrouter", "groq"):
        raise HTTPException(400, "provider harus openrouter atau groq")
    if prov not in ("openrouter", "groq"):
        prov = "openrouter"

    def _must_valid(p, k):
        if not k:
            return
        chk = validate_provider_key(KeyCheck(provider=p, api_key=k))
        if not chk.get("ok"):
            raise HTTPException(400, chk.get("message") or "API key tidak valid")

    if body.openrouter_api_key is not None and str(body.openrouter_api_key).strip():
        _must_valid("openrouter", str(body.openrouter_api_key).strip())
    if body.groq_api_key is not None and str(body.groq_api_key).strip():
        _must_valid("groq", str(body.groq_api_key).strip())

    LLM_PROVIDER = prov
    _save_setting("llm_provider", prov)
    if body.openrouter_api_key is not None and str(body.openrouter_api_key).strip():
        OPENROUTER_API_KEY = str(body.openrouter_api_key).strip()
        _save_setting("openrouter_api_key", OPENROUTER_API_KEY)
    if body.groq_api_key is not None and str(body.groq_api_key).strip():
        GROQ_API_KEY = str(body.groq_api_key).strip()
        _save_setting("groq_api_key", GROQ_API_KEY)
    if body.openrouter_model:
        OPENROUTER_MODEL = body.openrouter_model.strip()
        _save_setting("openrouter_model", OPENROUTER_MODEL)
    if body.groq_model:
        GROQ_MODEL = body.groq_model.strip()
        _save_setting("groq_model", GROQ_MODEL)
    
    if body.mikrotik_host is not None:
        MIKROTIK_HOST = str(body.mikrotik_host).strip()
        _save_setting("mikrotik_host", MIKROTIK_HOST)
    if body.mikrotik_user is not None:
        MIKROTIK_USER = str(body.mikrotik_user).strip()
        _save_setting("mikrotik_user", MIKROTIK_USER)
    if body.mikrotik_pass is not None and str(body.mikrotik_pass).strip():
        MIKROTIK_PASS = str(body.mikrotik_pass).strip()
        _save_setting("mikrotik_pass", MIKROTIK_PASS)
    if body.mikrotik_port is not None and str(body.mikrotik_port).strip():
        MIKROTIK_PORT = str(body.mikrotik_port).strip()
        _save_setting("mikrotik_port", MIKROTIK_PORT)
    if body.api_token is not None:
        API_TOKEN = str(body.api_token).strip()
        _save_setting("api_token", API_TOKEN)
    rebuild_client()
    try:
        mt.reconfigure()
    except Exception:
        pass
    return get_provider()



def _list_models_for_provider(prov: str) -> list:
    items = []
    ids = set()
    if prov == "groq":
        for m in GROQ_FREE_MODELS:
            items.append({**m, "provider": "groq"})
            ids.add(m["id"])
        key = (GROQ_API_KEY or "").strip()
        if key:
            try:
                r = requests.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    for m in r.json().get("data", []):
                        mid = m.get("id") or ""
                        if not mid or mid in ids:
                            continue
                        low = mid.lower()
                        if any(x in low for x in (
                            "whisper", "tts", "guard", "prompt-guard", "orpheus", "playai",
                            "distil-whisper", "safety",
                        )):
                            continue
                        ids.add(mid)
                        items.append({"id": mid, "label": mid.split("/")[-1], "context": "?", "provider": "groq"})
            except Exception as e:
                console.print(f"[dim]groq models: {e}[/dim]")
        return items
    for m in FREE_MODELS:
        items.append({**m, "provider": "openrouter"})
        ids.add(m["id"])
    key = (OPENROUTER_API_KEY or "").strip()
    if key:
        try:
            r = requests.get(
                "https://openrouter.ai/api/v1/models",
                timeout=8,
                headers={"Authorization": f"Bearer {key}"},
            )
            if r.ok:
                for m in r.json().get("data", []):
                    mid = m.get("id") or ""
                    pricing = m.get("pricing") or {}
                    try:
                        prompt = float(pricing.get("prompt") or 1)
                        completion = float(pricing.get("completion") or 1)
                    except Exception:
                        prompt = completion = 1
                    is_free = (":free" in mid) or (prompt == 0 and completion == 0)
                    if not is_free or mid in ids:
                        continue
                    ids.add(mid)
                    items.append({
                        "id": mid,
                        "label": (m.get("name") or mid).replace(" (free)", ""),
                        "context": str(m.get("context_length") or "?"),
                        "provider": "openrouter",
                    })
        except Exception as e:
            console.print(f"[dim]openrouter models: {e}[/dim]")
    return items



@app.post("/model")
def set_model(body: ModelUpdate, cred: HTTPAuthorizationCredentials = Depends(security)):
    global OPENROUTER_MODEL, GROQ_MODEL
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    mid = (body.model or "").strip()
    if not mid:
        raise HTTPException(400, "model kosong")
    if LLM_PROVIDER == "groq":
        GROQ_MODEL = mid
        _save_setting("groq_model", mid)
    else:
        OPENROUTER_MODEL = mid
        _save_setting("openrouter_model", mid)
    console.print(f"[green]Model → {mid} ({LLM_PROVIDER})[/green]")
    return {"ok": True, "model": mid, "provider": LLM_PROVIDER}


@app.get("/metrics")
def metrics():
    """Live router metrics via MikroTik REST only. Does NOT use OpenRouter tokens."""
    return collect_live_metrics()



def _history_for_display(hist: list) -> list:
    out = []
    for m in hist or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content") or ""
        if role in ("user", "assistant") and str(content).strip():
            out.append({
                "role": role,
                "content": content,
                "created_at": m.get("created_at") or "",
            })
    return out


def load_session_messages_page(sid: str, offset: int = 0, limit: int = 30) -> dict:
    """Paged messages for infinite scroll. offset from end: 0 = newest page."""
    import json as _json
    limit = max(1, min(int(limit or 30), 100))
    offset = max(0, int(offset or 0))

    def from_hist(hist):
        disp = _history_for_display(hist if isinstance(hist, list) else [])
        total = len(disp)
        if offset == 0:
            page = disp[-limit:] if total > limit else disp
        else:
            end = max(0, total - offset)
            start = max(0, end - limit)
            page = disp[start:end]
        return {
            "messages": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < total,
        }

    try:
        if STORAGE_BACKEND == "sqlite":
            db = _db()
            # pastikan tabel ada (DB lama)
            try:
                db.execute("SELECT 1 FROM messages LIMIT 1")
            except Exception:
                _ensure_messages_from_sessions_json()
            try:
                total = db.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE session_id=? AND role IN ('user','assistant') AND trim(IFNULL(content,'')) != ''",
                    (sid,),
                ).fetchone()["c"]
            except Exception:
                total = 0
            if total == 0:
                row = db.execute(
                    "SELECT messages_json FROM sessions WHERE session_id=?", (sid,)
                ).fetchone()
                if row:
                    try:
                        hist = _json.loads(row["messages_json"] or "[]")
                    except Exception:
                        hist = []
                    return from_hist(hist)
                return from_hist(sessions.get(sid) or [])
            rows = db.execute(
                """
                SELECT id, seq, role, content, created_at FROM messages
                WHERE session_id=? AND role IN ('user','assistant') AND trim(IFNULL(content,'')) != ''
                ORDER BY seq DESC
                LIMIT ? OFFSET ?
                """,
                (sid, limit, offset),
            ).fetchall()
            msgs = [
                {
                    "role": r["role"],
                    "content": r["content"],
                    "seq": r["seq"],
                    "created_at": r["created_at"] or "",
                }
                for r in reversed(list(rows))
            ]
            return {
                "messages": msgs,
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": (offset + limit) < total,
            }
        return from_hist(sessions.get(sid) or [])
    except Exception as e:
        console.print(f"[yellow]load_session_messages_page: {e}[/yellow]")
        return from_hist(sessions.get(sid) or [])



@app.post("/chat")
def chat(req: ChatRequest, cred: HTTPAuthorizationCredentials = Depends(security)):
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    sid = req.session_id or str(uuid.uuid4())
    lang = (getattr(req, "language", None) or "en").lower().strip()
    if lang not in ("en", "id"):
        lang = "en"
    if sid not in sessions:
        sessions[sid] = [{"role": "system", "content": build_system_prompt(getattr(req, "language", None) or "en")}]

    # Local guard — no external AI call when key/model/router missing
    def _router_configured() -> bool:
        return bool((MIKROTIK_HOST or "").strip() and (MIKROTIK_USER or "").strip() and (MIKROTIK_PASS or "").strip())

    if not _active_api_key() and not _router_configured():
        reply = (
            "Setup incomplete\n\n"
            "Open Settings (gear) and fill in:\n"
            "• API Key (OpenRouter or Groq)\n"
            "• MikroTik IP / Host\n"
            "• MikroTik user\n"
            "• MikroTik password\n\n"
            "Save, then send your message again."
            if lang == "en" else
            "Pengaturan belum lengkap\n\n"
            "Isi lewat ikon Pengaturan (gear):\n"
            "• API Key (OpenRouter atau Groq)\n"
            "• IP / Host MikroTik\n"
            "• User MikroTik\n"
            "• Password MikroTik\n\n"
            "Simpan, lalu kirim ulang pesan Anda."
        )
        sessions[sid].append({"role": "user", "content": req.message})
        sessions[sid].append({"role": "assistant", "content": reply, "created_at": ""})
        touch_session_meta(sid, getattr(req, "title", None), provider=LLM_PROVIDER)
        try:
            save_sessions(sessions)
        except Exception:
            pass
        return {
            "reply": reply,
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
            "key_required": True,
        }
    if not _active_api_key():
        reply = (
            "API Key Required\n\n"
            "No active API key.\n\n"
            "Open Settings, choose OpenRouter or Groq, paste the API key, "
            "verify Valid, then Save & apply. Send your message again."
            if lang == "en" else
            "API Key Required\n\n"
            "Belum ada API key yang aktif.\n\n"
            "Buka Pengaturan, pilih OpenRouter atau Groq, tempel API key, "
            "cek Valid, lalu Simpan & terapkan. Setelah itu kirim ulang pesan Anda."
        )
        sessions[sid].append({"role": "user", "content": req.message})
        sessions[sid].append({"role": "assistant", "content": reply, "created_at": ""})
        touch_session_meta(sid, getattr(req, "title", None), provider=LLM_PROVIDER)
        try:
            save_sessions(sessions)
        except Exception:
            pass
        return {
            "reply": reply,
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
            "key_required": True,
        }
    if not _router_configured():
        reply = (
            "Router connection not configured\n\n"
            "API key is set. Complete in Settings:\n"
            "• MikroTik IP / Host\n"
            "• MikroTik user\n"
            "• MikroTik password\n\n"
            "Save, then send your message again."
            if lang == "en" else
            "Koneksi router belum diatur\n\n"
            "API key sudah ada. Lengkapi di Pengaturan:\n"
            "• IP / Host MikroTik\n"
            "• User MikroTik\n"
            "• Password MikroTik\n\n"
            "Simpan, lalu kirim ulang pesan Anda."
        )
        sessions[sid].append({"role": "user", "content": req.message})
        sessions[sid].append({"role": "assistant", "content": reply, "created_at": ""})
        touch_session_meta(sid, getattr(req, "title", None), provider=LLM_PROVIDER)
        try:
            save_sessions(sessions)
        except Exception:
            pass
        return {
            "reply": reply,
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
            "key_required": False,
        }
    if not (_active_model() or "").strip():
        reply = (
            "No AI model selected.\n\n"
            "After the API key is valid, pick a model in the AI Engine dropdown and send again."
            if lang == "en" else
            "Model AI belum dipilih.\n\n"
            "Setelah API key valid, pilih model di dropdown AI Engine lalu kirim ulang instruksi Anda."
        )
        sessions[sid].append({"role": "user", "content": req.message})
        sessions[sid].append({"role": "assistant", "content": reply, "created_at": ""})
        touch_session_meta(sid, getattr(req, "title", None), provider=LLM_PROVIDER)
        try:
            save_sessions(sessions)
        except Exception:
            pass
        return {
            "reply": reply,
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
            "key_required": True,
        }


    try:
        reply, hist = run_conversation(req.message, sessions[sid], lang=lang)
        sessions[sid] = hist
        touch_session_meta(sid, getattr(req, "title", None), provider=LLM_PROVIDER)
        try:
            save_sessions(sessions)
        except Exception as se:
            console.print(f"[yellow]save_sessions: {se}[/yellow]")
        return {
            "reply": reply,
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
        }
    except Exception as e:
        console.print(f"[red]chat failed: {e}[/red]")
        # Jangan lempar 500 polos — UI bisa tampilkan pesan
        return {
            "reply": (
                f"❌ **Internal error**: `{str(e)[:400]}`"
            ),
            "session_id": sid,
            "dry_run": DRY_RUN,
            "require_confirmation": REQUIRE_CONFIRMATION,
            "error": True,
        }




@app.get("/sessions")
def list_sessions(cred: HTTPAuthorizationCredentials = Depends(security)):
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    meta = load_session_meta()
    # include sessions that exist in memory/store even without meta
    for sid in list(sessions.keys()):
        if sid not in meta:
            meta[sid] = {"title": f"Sesi {sid[:8]}", "updated_at": ""}
    items = [
        {
            "session_id": sid,
            "title": v.get("title") or f"Sesi {sid[:8]}",
            "created_at": v.get("created_at") or v.get("updated_at") or "",
            "updated_at": v.get("updated_at") or v.get("created_at") or "",
            "provider": v.get("provider") or "openrouter",
        }
        for sid, v in meta.items()
    ]
    items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)  # terbaru di atas
    return {"sessions": items}


class SessionUpdate(BaseModel):
    title: str


@app.patch("/sessions/{sid}")
def rename_session(sid: str, body: SessionUpdate, cred: HTTPAuthorizationCredentials = Depends(security)):
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    touch_session_meta(sid, body.title)
    return {"ok": True, "session_id": sid, "title": body.title.strip()[:80]}


@app.delete("/sessions/{sid}")
def delete_session(sid: str, cred: HTTPAuthorizationCredentials = Depends(security)):
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    sessions.pop(sid, None)
    # Hapus langsung dari SQLite agar tidak muncul lagi setelah restart
    try:
        if STORAGE_BACKEND == "sqlite":
            db = _db()
            db.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
            db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            db.execute("DELETE FROM session_meta WHERE session_id=?", (sid,))
            db.commit()
    except Exception as e:
        console.print(f"[yellow]delete_session db: {e}[/yellow]")
    try:
        save_sessions(sessions)
    except Exception as e:
        console.print(f"[yellow]delete_session save_sessions: {e}[/yellow]")
    meta = load_session_meta()
    meta.pop(sid, None)
    save_session_meta(meta)
    return {"ok": True, "deleted": sid}


@app.get("/sessions/{sid}")
def get_session(sid: str, cred: HTTPAuthorizationCredentials = Depends(security)):
    """Meta + halaman terakhir (UI). Full history di SQLite / memori agent."""
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    try:
        hist = sessions.get(sid)
        if hist is None:
            disk = load_sessions()
            hist = disk.get(sid) or []
            if hist:
                sessions[sid] = hist
        meta = load_session_meta().get(sid) or {}
        page = load_session_messages_page(sid, offset=0, limit=200)
        return {
            "session_id": sid,
            "title": meta.get("title") or f"Sesi {sid[:8]}",
            "created_at": meta.get("created_at") or meta.get("updated_at") or "",
            "updated_at": meta.get("updated_at") or meta.get("created_at") or "",
            "messages": page.get("messages") or [],
            "total": page.get("total") or 0,
            "has_more": bool(page.get("has_more")),
            "offset": 0,
            "limit": 80,
        }
    except Exception as e:
        console.print(f"[red]get_session error: {e}[/red]")
        console.print(f"[yellow]get_session soft-fail: {e}[/yellow]")
        meta = load_session_meta().get(sid) or {}
        return {
            "session_id": sid,
            "title": meta.get("title") or f"Sesi {sid[:8]}",
            "messages": [],
            "total": 0,
            "has_more": False,
            "offset": 0,
            "limit": 30,
            "error": str(e),
        }


@app.get("/sessions/{sid}/messages")
def get_session_messages(
    sid: str,
    offset: int = 0,
    limit: int = 30,
    cred: HTTPAuthorizationCredentials = Depends(security),
):
    """Infinite scroll: offset dari ujung (0 = terbaru)."""
    if API_TOKEN and (not cred or cred.credentials != API_TOKEN):
        raise HTTPException(401, "Unauthorized")
    if sid not in sessions:
        disk = load_sessions()
        if sid in disk:
            sessions[sid] = disk[sid]
    return load_session_messages_page(sid, offset=offset, limit=limit)


def run_http():
    console.print(Panel.fit("[green]MikroTik AI Control ready[/green]\nOpen the browser UI to continue."))
    uvicorn.run(app, host=HTTP_HOST, port=HTTP_PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    ensure_data_dir()
    seed_default_memory()
    console.print(f"[dim]Storage: {STORAGE_BACKEND} · path: {SQLITE_PATH if STORAGE_BACKEND == 'sqlite' else MEMORY_FILE}[/dim]")
    if MODE == "http":
        run_http()
    else:
        run_cli()

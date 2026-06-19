"""
Spinner model routing — the LOCKED resolution (2026-06-19), env-tunable.

Precise trigger per task, NOT a blanket. Verified-benchmark builder = DeepSeek V4
(only one with independent SWE-bench). Skip Qwen Max (overkill for a lightweight app).
Token-grant ceiling is budgeted at the worst-case of these (Kimi K2.7 $3.41/M out).

Anything above fork scope (full-stack / frontend / multi-service) is NOT routed here —
it escalates to Charlotte upstream.
"""
import os

ROUTES = {
    # build / integration code → DeepSeek V4 (Flash default, Pro for large/critical)
    "build":        os.environ.get("SPINNER_MODEL_BUILD",        "deepseek/deepseek-v4-flash"),
    "build_heavy":  os.environ.get("SPINNER_MODEL_BUILD_HEAVY",  "deepseek/deepseek-v4-pro"),
    # general chat + the 6 lenses → DeepSeek V4 Flash (the cheap, fast workhorse)
    "chat":         os.environ.get("SPINNER_MODEL_CHAT",         "deepseek/deepseek-v4-flash"),
    # multilingual / vision (read a URL, screenshot, non-English) → Qwen 3.7 Plus
    "multilingual": os.environ.get("SPINNER_MODEL_MULTILINGUAL", "qwen/qwen3.7-plus"),
    "vision":       os.environ.get("SPINNER_MODEL_VISION",       "qwen/qwen3.7-plus"),
    # MCP-heavy tool/function calling → Kimi K2.7 (its real lane)
    "mcp_tools":    os.environ.get("SPINNER_MODEL_MCP",          "moonshotai/kimi-k2.7-code"),
    # free tier stays on a $0 model (never reaches a paid model — fails closed)
    "free":         os.environ.get("SPINNER_MODEL_FREE",         "deepseek/deepseek-v4-flash:free"),
}


def route(task: str, *, free: bool = False) -> str:
    """Return the model slug for a task. free=True forces the $0 tier."""
    if free:
        return ROUTES["free"]
    return ROUTES.get(task, ROUTES["chat"])

"""Normalize RunnableConfig.callbacks before LangGraph merge_configs.

LangGraph's ``langgraph._internal._config.merge_configs`` only merges the
``callbacks`` key when values are a ``list`` or ``BaseCallbackManager``.
Tracing stacks often inject a single handler, a ``tuple``, a ``deque``, or
configs that are ``ChainMap``/``Mapping`` (not plain ``dict``), which can hit a
bare ``NotImplementedError`` inside LangGraph.

This module wraps ``langgraph._internal._config.merge_configs`` at import and
rebinds every loaded ``langgraph.*`` submodule that still holds a stale
reference to the original function (``from _config import merge_configs``).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Mapping, Sequence
from typing import Any

_installed = False
_lock = threading.Lock()


def _normalize_one(config: Any) -> Any:
    if config is None or not isinstance(config, Mapping):
        return config
    if "callbacks" not in config:
        return config
    cb = config.get("callbacks")
    if cb is None or cb is False:
        return config

    from langchain_core.callbacks import BaseCallbackManager

    if isinstance(cb, BaseCallbackManager):
        return config
    if isinstance(cb, list):
        return config

    # Sequences of handlers (tuple, deque, etc.) — but not str/bytes.
    if isinstance(cb, Sequence) and not isinstance(cb, (str, bytes)):
        out = dict(config)
        out["callbacks"] = list(cb)
        return out

    # Single handler or other non-mergeable shape LangGraph rejects.
    out = dict(config)
    out["callbacks"] = [cb]
    return out


def install_merge_configs_patch() -> None:
    global _installed
    with _lock:
        if _installed:
            return

        import langgraph._internal._config as lg

        raw = lg.merge_configs
        while getattr(raw, "_wakilai_callbacks_patch", False):
            inner = getattr(raw, "_wakilai_merge_inner", None)
            if inner is None:
                break
            raw = inner

        original = raw
        if getattr(original, "_wakilai_callbacks_patch", False):
            _installed = True
            return

        def merge_configs(*configs: Any) -> Any:
            fixed = tuple(_normalize_one(c) for c in configs)
            return original(*fixed)

        merge_configs._wakilai_callbacks_patch = True  # type: ignore[attr-defined]
        merge_configs._wakilai_merge_inner = original  # type: ignore[attr-defined]
        lg.merge_configs = merge_configs  # type: ignore[assignment]

        # Rebind stale ``from langgraph._internal._config import merge_configs``.
        for name, mod in list(sys.modules.items()):
            if not name.startswith("langgraph"):
                continue
            if getattr(mod, "merge_configs", None) is original:
                setattr(mod, "merge_configs", merge_configs)

        _installed = True


install_merge_configs_patch()

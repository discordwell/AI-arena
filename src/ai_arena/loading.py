from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


@dataclass(frozen=True, slots=True)
class LoadSpec:
    path: Path
    symbol: str


def parse_load_spec(spec: str) -> LoadSpec:
    """
    Parse "<path>:<symbol>".

    Examples:
      - "codex/game/game.py:CodexGame"
      - "/abs/path/agent.py:AgentFactory"
    """
    if ":" not in spec:
        raise ValueError(f"Expected '<path>:<symbol>', got: {spec!r}")
    path_str, symbol = spec.rsplit(":", 1)
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if not symbol:
        raise ValueError(f"Missing symbol in spec: {spec!r}")
    return LoadSpec(path=path, symbol=symbol)


def load_module_from_path(path: Path, *, module_name_hint: str = "ai_arena_dynamic") -> ModuleType:
    module_name = f"{module_name_hint}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_symbol(spec: str) -> Any:
    s = parse_load_spec(spec)
    module = load_module_from_path(s.path)
    try:
        return getattr(module, s.symbol)
    except AttributeError as e:
        raise AttributeError(f"{s.path} has no symbol {s.symbol!r}") from e

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .greedy import GreedyAgent
from .mcts import MctsAgent
from .random_agent import RandomAgent
from .search import SearchAgent

# Tunable built-in agents and the strength knobs each one exposes.
#
# A spec is either a bare name (``"search"``) or a name with a comma-separated
# parameter list (``"search:max_depth=6,node_budget=20000"``). The parameters
# mirror the agents' own constructor fields, so the benchmarking/round-robin
# tooling can dial a baseline's strength (e.g. pit an LLM bot against a stronger
# ``mcts``, or compare ``search`` at several depths) without editing code. The
# bare-name forms keep their defaults, so existing specs behave identically.
#
# ``seed`` is deliberately NOT listed here: every caller supplies it through its
# own policy (``play --seed`` derives per-seat seeds; ``benchmark`` derives a
# distinct per-game seed; the tournament/GUI leave built-ins unseeded), so it is
# passed alongside these parameters rather than parsed from the spec.


@dataclass(frozen=True, slots=True)
class _Param:
    """One tunable constructor parameter: its type and inclusive lower bound."""

    kind: type  # int or float
    min_value: float

    def coerce(self, agent: str, key: str, raw: str) -> Any:
        try:
            value = self.kind(raw)
        except ValueError:
            raise ValueError(
                f"'{agent}' parameter {key!r} must be {self.kind.__name__}, got: {raw!r}"
            ) from None
        # `float("inf")` / `float("nan")` parse fine but would silently break the
        # agents that consume them (e.g. NaN poisons MCTS's UCT comparisons).
        if self.kind is float and not math.isfinite(value):
            raise ValueError(f"'{agent}' parameter {key!r} must be finite, got: {raw!r}")
        if value < self.min_value:
            # Render an int bound as an int so the message reads "1" not "1.0".
            bound = int(self.min_value) if self.min_value == int(self.min_value) else self.min_value
            raise ValueError(f"'{agent}' parameter {key!r} must be >= {bound}, got: {value}")
        return value


# name -> (agent class, {param name: spec}). The class is constructed as
# ``cls(seed=<caller's seed>, **parsed_params)``; every built-in here takes a
# ``seed`` keyword.
BUILTIN_AGENTS: dict[str, tuple[type, dict[str, _Param]]] = {
    "random": (RandomAgent, {}),
    "greedy": (GreedyAgent, {"safety_budget": _Param(int, 1)}),
    "search": (
        SearchAgent,
        {"max_depth": _Param(int, 1), "node_budget": _Param(int, 1)},
    ),
    "mcts": (
        MctsAgent,
        {
            "iterations": _Param(int, 1),
            "node_budget": _Param(int, 1),
            "rollout_depth": _Param(int, 1),
            "exploration": _Param(float, 0.0),
        },
    ),
}


def _parse_params(agent: str, tail: str, allowed: dict[str, _Param]) -> dict[str, Any]:
    """Parse ``"k=v,k=v"`` into validated, type-coerced kwargs for ``agent``."""
    tokens = [t.strip() for t in tail.split(",")]
    tokens = [t for t in tokens if t]  # tolerate a trailing/empty comma
    if not tokens:
        return {}
    if not allowed:
        raise ValueError(f"the {agent!r} agent takes no parameters (got: {tail!r})")

    out: dict[str, Any] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"agent parameter must be key=value, got: {token!r}")
        key, _, raw = token.partition("=")
        key, raw = key.strip(), raw.strip()
        param = allowed.get(key)
        if param is None:
            allowed_list = ", ".join(sorted(allowed))
            raise ValueError(f"unknown {agent!r} parameter {key!r}; allowed: {allowed_list}")
        if key in out:
            raise ValueError(f"duplicate {agent!r} parameter {key!r}")
        out[key] = param.coerce(agent, key, raw)
    return out


def resolve_builtin_agent(spec: str) -> tuple[type, dict[str, Any]] | None:
    """
    Resolve a built-in agent spec, optionally carrying tunable parameters.

    Returns ``(AgentClass, kwargs)`` to construct as ``AgentClass(seed=...,
    **kwargs)`` (the caller adds its own ``seed``), or ``None`` when ``spec`` is
    not a built-in agent name -- so the caller falls through to its ``human`` /
    ``subprocess:<cmd>`` / ``<path>:<symbol>`` handling. Only the four seedable
    built-ins (``random`` / ``greedy`` / ``search`` / ``mcts``) are recognised
    here; their names are reserved spec heads.

    Raises ``ValueError`` with a clear message on an unknown parameter, a
    malformed ``key=value`` token, a non-numeric value, or an out-of-range value.
    """
    head, sep, tail = spec.partition(":")
    entry = BUILTIN_AGENTS.get(head)
    if entry is None:
        return None
    cls, allowed = entry
    kwargs = _parse_params(head, tail, allowed) if sep else {}
    return cls, kwargs

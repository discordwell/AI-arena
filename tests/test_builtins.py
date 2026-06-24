"""Tests for the tunable built-in agent resolver (ai_arena.agents.builtins)."""

from __future__ import annotations

import pytest

from ai_arena.agents.builtins import BUILTIN_AGENTS, resolve_builtin_agent
from ai_arena.agents.greedy import GreedyAgent
from ai_arena.agents.mcts import MctsAgent
from ai_arena.agents.random_agent import RandomAgent
from ai_arena.agents.search import SearchAgent


def test_bare_names_resolve_to_classes_with_no_params() -> None:
    # A bare built-in name carries no parameters, so construction matches today's
    # defaults exactly (the feature is purely additive).
    assert resolve_builtin_agent("random") == (RandomAgent, {})
    assert resolve_builtin_agent("greedy") == (GreedyAgent, {})
    assert resolve_builtin_agent("search") == (SearchAgent, {})
    assert resolve_builtin_agent("mcts") == (MctsAgent, {})


def test_non_builtin_specs_fall_through_to_none() -> None:
    # human / subprocess / <path>:<symbol> are handled by the caller, not here.
    assert resolve_builtin_agent("human") is None
    assert resolve_builtin_agent("subprocess:python3 bot.py") is None
    assert resolve_builtin_agent("codex/game/game.py:CodexGame") is None
    assert resolve_builtin_agent("/abs/path/agent.py:MyAgent") is None


def test_parses_and_coerces_int_and_float_params() -> None:
    cls, kwargs = resolve_builtin_agent("search:max_depth=6,node_budget=20000")
    assert cls is SearchAgent
    assert kwargs == {"max_depth": 6, "node_budget": 20000}
    assert isinstance(kwargs["max_depth"], int)

    cls, kwargs = resolve_builtin_agent("mcts:iterations=2000,exploration=1.0")
    assert cls is MctsAgent
    assert kwargs == {"iterations": 2000, "exploration": 1.0}
    assert isinstance(kwargs["exploration"], float)


def test_partial_params_keep_other_defaults() -> None:
    # Only the named knob changes; unspecified ones fall back to the agent's
    # constructor default.
    cls, kwargs = resolve_builtin_agent("search:max_depth=4")
    agent = cls(seed=7, **kwargs)
    assert agent.max_depth == 4
    assert agent.node_budget == 40_000  # untouched default


def test_resolved_params_land_on_the_instance() -> None:
    cls, kwargs = resolve_builtin_agent("mcts:iterations=50,node_budget=900,exploration=0.5")
    agent = cls(seed=1, **kwargs)
    assert (agent.iterations, agent.node_budget, agent.exploration) == (50, 900, 0.5)
    assert agent.name == "mcts"


def test_whitespace_and_trailing_comma_tolerated() -> None:
    cls, kwargs = resolve_builtin_agent("search: max_depth = 6 , node_budget=10 ,")
    assert kwargs == {"max_depth": 6, "node_budget": 10}


def test_trailing_colon_with_no_params_is_empty() -> None:
    assert resolve_builtin_agent("search:") == (SearchAgent, {})


@pytest.mark.parametrize(
    "spec, fragment",
    [
        ("search:max_depth=abc", "must be int"),
        ("search:max_depth=1.5", "must be int"),  # int('1.5') is not valid
        ("mcts:exploration=hot", "must be float"),
        ("search:max_depth=0", "must be >= 1"),
        ("search:node_budget=-3", "must be >= 1"),
        ("mcts:exploration=-0.5", "must be >= 0"),
        ("mcts:exploration=inf", "must be finite"),
        ("mcts:exploration=nan", "must be finite"),
        ("search:bogus=3", "unknown 'search' parameter"),
        ("search:max_depth", "key=value"),
        ("search:max_depth=3,max_depth=4", "duplicate"),
        ("random:seed=5", "takes no parameters"),
        ("greedy:foo=1", "unknown 'greedy' parameter"),
    ],
)
def test_invalid_params_raise_clear_value_errors(spec: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        resolve_builtin_agent(spec)


def test_unknown_param_message_lists_allowed_names() -> None:
    with pytest.raises(ValueError, match="allowed: max_depth, node_budget"):
        resolve_builtin_agent("search:typo=1")


def test_gui_loader_applies_tuned_params() -> None:
    # The GUI loader is the fourth site wired to the resolver; verify a tuned spec
    # reaches the constructed agent there too. (Skips if Tkinter is unavailable,
    # since the GUI module imports it at load time.)
    gui = pytest.importorskip("ai_arena.gui")
    agent = gui._load_agent("search:max_depth=3")
    assert agent.name == "search"
    assert agent.max_depth == 3


def test_registry_covers_exactly_the_seedable_builtins() -> None:
    # Drift guard: the resolver registry and the CLI's advertised built-ins must
    # stay in sync. `list-agents` advertises the seedable agents plus `human`
    # (which is not tunable and is handled separately), so the registry keys are
    # exactly that list minus `human`.
    from ai_arena.cli import _BUILTIN_AGENTS

    assert set(BUILTIN_AGENTS) == set(_BUILTIN_AGENTS) - {"human"}

    # Every registered class is constructible with seed + its declared params and
    # exposes the agent protocol.
    for name, (cls, params) in BUILTIN_AGENTS.items():
        kwargs = {key: param.kind(param.min_value + 1) for key, param in params.items()}
        agent = cls(seed=0, **kwargs)
        assert agent.name == name
        assert callable(getattr(agent, "select_move", None))

"""
Tests for the shared spec resolver (ai_arena.specs) and a drift guard that the
four entry-point loaders (cli / gui / benchmark / tournament) stay in sync.

The dispatch from a spec string to a game/agent factory used to be copied inline
into each entry point, so the subprocess parsing, the built-in fall-through, and
the ``<path>:<symbol>`` loading could drift apart. They now all route through
``ai_arena.specs``; these tests pin both the resolver's behavior and the fact
that every entry point agrees with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_arena.agents.random_agent import RandomAgent
from ai_arena.agents.search import SearchAgent
from ai_arena.games.tictactoe import TicTacToe
from ai_arena.specs import (
    BUILTIN_GAMES,
    is_subprocess_spec,
    parse_subprocess_command,
    resolve_agent_factory,
    resolve_game_factory,
)

# A tiny standalone game/agent written to a file, exercised through the
# ``<path>:<symbol>`` branch exactly as a model folder ships one.
_MINI_GAME = (
    "from ai_arena.game import Terminal\n"
    "class MiniGame:\n"
    "    name = 'mini'\n"
    "    def initial_state(self):\n"
    "        return {'n': 0}\n"
    "    def legal_moves(self, state, player):\n"
    "        return [1]\n"
    "    def apply_move(self, state, player, move):\n"
    "        return {'n': state['n'] + move}\n"
    "    def terminal(self, state):\n"
    "        return Terminal(state['n'] >= 3, 0 if state['n'] >= 3 else None, 'win')\n"
    "    def render(self, state):\n"
    "        return str(state['n'])\n"
)

_MINI_AGENT = (
    "class MiniAgent:\n"
    "    name = 'mini'\n"
    "    def select_move(self, game, state, player, legal_moves):\n"
    "        return legal_moves[0]\n"
)


# --- resolve_game_factory --------------------------------------------------


def test_resolve_game_factory_builtin_returns_fresh_instances() -> None:
    factory = resolve_game_factory("tictactoe")
    g1, g2 = factory(), factory()
    assert isinstance(g1, TicTacToe) and isinstance(g2, TicTacToe)
    assert g1 is not g2  # a factory, not a shared singleton


def test_resolve_game_factory_loads_path_spec(tmp_path: Path) -> None:
    p = tmp_path / "g.py"
    p.write_text(_MINI_GAME, encoding="utf-8")
    game = resolve_game_factory(f"{p}:MiniGame")()
    assert game.name == "mini"
    assert game.initial_state() == {"n": 0}


def test_builtin_games_registry_backs_list_games() -> None:
    # `cli list-games` iterates BUILTIN_GAMES; keep it non-empty and resolvable.
    assert "tictactoe" in BUILTIN_GAMES
    for name in BUILTIN_GAMES:
        assert resolve_game_factory(name)().name == name


# --- resolve_agent_factory -------------------------------------------------


def test_resolve_agent_factory_threads_seed_into_builtins() -> None:
    make = resolve_agent_factory("random")
    assert make(7).seed == 7  # the seed reaches the seedable built-in
    assert make(None).seed is None


def test_resolve_agent_factory_applies_tuned_params() -> None:
    agent = resolve_agent_factory("search:max_depth=3")(1)
    assert isinstance(agent, SearchAgent)
    assert agent.max_depth == 3 and agent.seed == 1


def test_resolve_agent_factory_rejects_bad_param_eagerly() -> None:
    # A bad knob fails at resolution time (before any match), not at first move.
    with pytest.raises(ValueError, match="must be >= 1"):
        resolve_agent_factory("search:max_depth=0")


def test_resolve_agent_factory_builds_fresh_instances() -> None:
    make = resolve_agent_factory("random")
    assert make(0) is not make(0)  # each game gets its own agent


def test_resolve_agent_factory_loads_path_spec(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text(_MINI_AGENT, encoding="utf-8")
    agent = resolve_agent_factory(f"{p}:MiniAgent")(123)  # seed ignored, no error
    assert agent.name == "mini"


# --- subprocess parsing (the string that was copied four times) ------------


def test_parse_subprocess_command_splits_argv() -> None:
    assert parse_subprocess_command("subprocess:python3 -u bot.py") == ["python3", "-u", "bot.py"]
    assert is_subprocess_spec("subprocess:x") and not is_subprocess_spec("random")


@pytest.mark.parametrize("spec", ["subprocess:", "subprocess:   "])
def test_parse_subprocess_command_rejects_empty(spec: str) -> None:
    with pytest.raises(ValueError, match="subprocess agent requires a command"):
        parse_subprocess_command(spec)


# --- drift guard: every entry point agrees with the resolver ---------------


def _entry_point_agents(spec: str) -> dict[str, object]:
    """Build ``spec`` through each of the four entry-point loaders."""
    from ai_arena import cli, tournament
    from ai_arena.benchmark import _seeded_agent_factory

    gui = pytest.importorskip("ai_arena.gui")
    return {
        "cli": cli._load_agent(spec, seed=None),
        "gui": gui._load_agent(spec),
        "tournament": tournament._agent_factory(spec)(),
        "benchmark": _seeded_agent_factory(spec)(None),
    }


def test_all_entry_points_resolve_the_same_agent_class() -> None:
    # The whole point of the consolidation: a spec resolves to the same agent
    # (same class, same tuned params) no matter which command built it.
    agents = _entry_point_agents("search:max_depth=5")
    classes = {name: type(a) for name, a in agents.items()}
    assert set(classes.values()) == {SearchAgent}, classes
    for a in agents.values():
        assert a.max_depth == 5


def test_seed_policy_is_consistent_across_entry_points() -> None:
    # Two seeding policies, one resolver: `play`/`benchmark` thread a seed into
    # the built-ins for reproducibility; `tournament`/`gui` leave them unseeded.
    from ai_arena import cli, tournament
    from ai_arena.benchmark import _seeded_agent_factory

    gui = pytest.importorskip("ai_arena.gui")

    assert cli._load_agent("random", seed=99).seed == 99
    assert _seeded_agent_factory("random")(99).seed == 99
    assert tournament._agent_factory("random")().seed is None
    assert gui._load_agent("random").seed is None


def test_all_entry_points_reject_empty_subprocess_consistently() -> None:
    from ai_arena import cli, tournament
    from ai_arena.benchmark import _seeded_agent_factory

    gui = pytest.importorskip("ai_arena.gui")

    for build in (
        lambda: cli._load_agent("subprocess:"),
        lambda: gui._load_agent("subprocess:"),
        lambda: tournament._agent_factory("subprocess:"),
        lambda: _seeded_agent_factory("subprocess:"),
    ):
        with pytest.raises(ValueError, match="subprocess agent requires a command"):
            build()


def test_all_entry_points_resolve_the_same_game(tmp_path: Path) -> None:
    from ai_arena import cli, tournament

    gui = pytest.importorskip("ai_arena.gui")

    p = tmp_path / "g.py"
    p.write_text(_MINI_GAME, encoding="utf-8")
    spec = f"{p}:MiniGame"

    names = {
        cli._load_game("tictactoe").name,
        gui._load_game("tictactoe").name,
        tournament._game_factory("tictactoe")().name,
        resolve_game_factory("tictactoe")().name,
    }
    assert names == {"tictactoe"}

    path_names = {
        cli._load_game(spec).name,
        gui._load_game(spec).name,
        tournament._game_factory(spec)().name,
    }
    assert path_names == {"mini"}

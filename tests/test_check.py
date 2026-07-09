"""Tests for the `check-game` protocol-conformance checker (ai_arena.check)."""
from __future__ import annotations

import pytest

from ai_arena.check import (
    FAIL,
    PASS,
    WARN,
    check_game,
    format_report,
)
from ai_arena.cli import main
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe
from ai_arena.loading import load_symbol

# Every home game, so the suite is also a regression guard that the real arena
# games conform (in particular Photon, whose laser-trace coordinates must be
# JSON lists so the state survives a round-trip).
ARENA_GAMES = [
    "tictactoe",
    "codex/game/game.py:CodexGame",
    "opus/game/game.py:OpusGame",
    "gemini/game/game.py:GeminiGame",
]


def _status(report, name: str) -> str:
    """The status of the check named ``name`` (KeyError-free, asserts presence)."""
    matches = [c.status for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r} in {[c.name for c in report.checks]}"
    return matches[0]


# ---------------------------------------------------------------------------
# Correct games pass
# ---------------------------------------------------------------------------


class _Counter:
    """A minimal, fully-correct game: both players step a counter to a draw.

    Used as the clean base that the broken doubles below each break in exactly
    one way, so a failing check pins the one defect.
    """

    name = "counter"

    def __init__(self, n: int = 4) -> None:
        self.n = n

    def initial_state(self):
        return {"k": 0}

    def legal_moves(self, state, player):
        return [] if state["k"] >= self.n else ["inc"]

    def apply_move(self, state, player, move):
        return {"k": state["k"] + 1}

    def terminal(self, state):
        if state["k"] >= self.n:
            return Terminal(is_terminal=True, winner=None, reason="done")
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state):
        return f"k={state['k']}"


def test_minimal_correct_game_passes() -> None:
    report = check_game(_Counter(), playouts=5, seed=1)
    assert report.ok
    assert all(c.status == PASS for c in report.checks), [
        (c.name, c.status, c.detail) for c in report.checks if c.status != PASS
    ]
    assert report.endings == {"draw": 5}


def test_tictactoe_passes_with_real_endings() -> None:
    report = check_game(TicTacToe(), playouts=20, seed=3)
    assert report.ok
    # Random tic-tac-toe produces a mix of wins and draws.
    assert sum(report.endings.values()) == 20
    assert set(report.endings) <= {"win:0", "win:1", "draw"}


@pytest.mark.parametrize("spec", ARENA_GAMES)
def test_arena_games_conform(spec: str) -> None:
    game = load_symbol(spec)() if spec != "tictactoe" else TicTacToe()
    report = check_game(game, playouts=12, seed=5)
    assert report.ok, f"{spec} failed: " + "; ".join(
        f"{c.name}: {c.detail}" for c in report.checks if c.status == FAIL
    )


# ---------------------------------------------------------------------------
# Each broken game fails exactly the check that catches its defect
# ---------------------------------------------------------------------------


class _MutatingLegal(_Counter):
    name = "mut_legal"

    def legal_moves(self, state, player):
        state["scratch"] = state.get("scratch", 0) + 1  # mutates its input
        return super().legal_moves(state, player) or ["inc"]


def test_legal_moves_mutation_caught() -> None:
    # The Photon-class bug: a read-only method mutating the state it is handed.
    report = check_game(_MutatingLegal(), playouts=3, seed=1)
    assert _status(report, "legal_moves") == FAIL
    assert not report.ok


class _MutatingApply(_Counter):
    name = "mut_apply"

    def apply_move(self, state, player, move):
        state["k"] += 1  # mutates the input instead of returning a fresh state
        return state


def test_apply_move_mutation_caught() -> None:
    report = check_game(_MutatingApply(), playouts=3, seed=1)
    assert _status(report, "apply_move") == FAIL
    assert not report.ok


class _TupleApply(_Counter):
    name = "tuple_apply"

    def apply_move(self, state, player, move):
        return {"k": state["k"] + 1, "pos": (1, 2)}  # tuple breaks JSON round-trip


def test_non_roundtrippable_state_caught() -> None:
    # Exactly the latent Photon bug `check-game` first surfaced: a state that
    # changes (tuple -> list) under a JSON round-trip.
    report = check_game(_TupleApply(), playouts=3, seed=1)
    assert _status(report, "apply_move") == FAIL
    assert _status(report, "playout/json") == FAIL
    assert not report.ok


class _NonJsonInitial(_Counter):
    name = "nonjson"

    def initial_state(self):
        return {"k": 0, "bad": {1, 2}}  # a set is not JSON-serializable


def test_non_json_initial_state_caught() -> None:
    report = check_game(_NonJsonInitial(), playouts=3, seed=1)
    assert _status(report, "initial_state") == FAIL
    assert not report.ok


class _NeverEnds:
    name = "never"

    def initial_state(self):
        return {"k": 0}

    def legal_moves(self, state, player):
        return ["loop"]

    def apply_move(self, state, player, move):
        return {"k": state["k"] + 1}

    def terminal(self, state):
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state):
        return "x"


def test_non_terminating_game_caught() -> None:
    report = check_game(_NeverEnds(), playouts=4, max_turns=10, seed=1)
    assert _status(report, "playout/terminates") == FAIL
    assert report.endings == {"max_turns": 4}
    assert not report.ok


def test_ending_exactly_on_the_cap_is_not_miscounted() -> None:
    # A game that becomes terminal on the move applied at the very last permitted
    # turn must be counted as its real ending, not as "max_turns". The playout
    # loop checks `terminal` only *before* each move, so with max_turns == n the
    # _Counter's draw lands exactly on the cap; without the post-loop terminal
    # re-check it would be logged as a non-termination and wrongly FAIL
    # playout/terminates (this mirrors the engine's own max_turns boundary fix).
    report = check_game(_Counter(n=4), playouts=5, max_turns=4, seed=1)
    assert report.endings == {"draw": 5}
    assert _status(report, "playout/terminates") == PASS
    assert report.ok


class _LegalApplyMismatch(_Counter):
    name = "mismatch"

    def legal_moves(self, state, player):
        return ["inc", "bogus"] if state["k"] < self.n else []

    def apply_move(self, state, player, move):
        if move == "bogus":
            raise ValueError("cannot apply bogus")
        return {"k": state["k"] + 1}


def test_legal_apply_disagreement_caught() -> None:
    # legal_moves advertises a move apply_move cannot apply.
    report = check_game(_LegalApplyMismatch(), playouts=3, seed=1)
    assert _status(report, "apply_move") == FAIL
    assert not report.ok


class _BadWinnerAtEnd(_Counter):
    name = "bad_winner"

    def terminal(self, state):
        if state["k"] >= self.n:
            return Terminal(is_terminal=True, winner=5, reason="huh")  # 5 is not a player
        return Terminal(is_terminal=False, winner=None, reason="")


def test_bad_winner_during_play_caught() -> None:
    # The bad winner appears only at the terminal state reached during a playout,
    # so the deep check, not the static one, must catch it.
    report = check_game(_BadWinnerAtEnd(), playouts=3, seed=1)
    assert _status(report, "playout/winner") == FAIL
    assert not report.ok


class _BadTerminalShape(_Counter):
    name = "bad_term"

    def terminal(self, state):
        return Terminal(is_terminal=False, winner=7, reason="")  # winner must be None/0/1


def test_malformed_terminal_caught() -> None:
    report = check_game(_BadTerminalShape(), playouts=2, seed=1)
    assert _status(report, "terminal") == FAIL
    assert not report.ok


class _BoolWinner(_Counter):
    name = "bool_winner"

    def terminal(self, state):
        # `True`/`False` sneak past a naive `in (None, 0, 1)` (bool subclasses int).
        return Terminal(is_terminal=False, winner=True, reason="")


def test_bool_winner_rejected() -> None:
    report = check_game(_BoolWinner(), playouts=2, seed=1)
    assert _status(report, "terminal") == FAIL
    assert not report.ok


class _RaisingTerminal(_Counter):
    name = "raise_term"

    def terminal(self, state):
        raise RuntimeError("boom")


def test_raising_terminal_caught_not_propagated() -> None:
    report = check_game(_RaisingTerminal(), playouts=2, seed=1)  # must not raise
    assert _status(report, "terminal") == FAIL
    assert not report.ok


class _EmptyName(_Counter):
    name = ""


def test_empty_name_caught() -> None:
    report = check_game(_EmptyName(), playouts=2, seed=1)
    assert _status(report, "name") == FAIL
    assert report.game_name is None
    assert not report.ok


class _LegalNotList(_Counter):
    name = "notlist"

    def legal_moves(self, state, player):
        return "inc"  # a str, not a list of moves


def test_legal_moves_must_be_list() -> None:
    report = check_game(_LegalNotList(), playouts=2, seed=1)
    assert _status(report, "legal_moves") == FAIL
    assert not report.ok


class _RenderNotStr(_Counter):
    name = "notstr"

    def render(self, state):
        return 42  # not a str


def test_render_must_return_str() -> None:
    report = check_game(_RenderNotStr(), playouts=2, seed=1)
    assert _status(report, "render") == FAIL
    assert not report.ok


class _DeadStart:
    name = "dead"

    def initial_state(self):
        return {}

    def legal_moves(self, state, player):
        return []  # neither player can move ...

    def apply_move(self, state, player, move):
        return state

    def terminal(self, state):
        return Terminal(is_terminal=False, winner=None, reason="")  # ... yet not terminal

    def render(self, state):
        return "x"


def test_dead_on_arrival_caught() -> None:
    report = check_game(_DeadStart(), playouts=2, seed=1)
    assert _status(report, "legal_moves") == FAIL
    assert not report.ok


class _TerminalAtStart(_Counter):
    """A game already terminal at its initial state: degenerate, but not a
    protocol violation (the engine ends it immediately), so it must not be
    flagged as 'dead on arrival'."""

    name = "term_start"

    def legal_moves(self, state, player):
        return []  # no moves, but ...

    def terminal(self, state):
        return Terminal(is_terminal=True, winner=0, reason="instant")  # ... terminal


def test_terminal_at_start_is_not_a_failure() -> None:
    report = check_game(_TerminalAtStart(), playouts=3, seed=1)
    assert _status(report, "legal_moves") == PASS
    assert report.ok
    assert report.endings == {"win:0": 3}


# ---------------------------------------------------------------------------
# The checker never crashes on a hostile game
# ---------------------------------------------------------------------------


class _RaiseEverything:
    name = "rage"

    def initial_state(self):
        return {"k": 0}

    def legal_moves(self, state, player):
        raise RuntimeError("x")

    def apply_move(self, state, player, move):
        raise RuntimeError("x")

    def terminal(self, state):
        raise RuntimeError("x")

    def render(self, state):
        raise RuntimeError("x")


def test_checker_survives_a_game_that_raises_everywhere() -> None:
    report = check_game(_RaiseEverything(), playouts=2, seed=1)  # must not raise
    assert not report.ok
    assert _status(report, "terminal") == FAIL
    assert _status(report, "render") == FAIL


class _NamePropertyRaises:
    """`name` is a property that raises -- reading it must not crash the checker."""

    @property
    def name(self):
        raise RuntimeError("name boom")

    def initial_state(self):
        return {"k": 0}

    def legal_moves(self, state, player):
        return [] if state["k"] >= 2 else ["inc"]

    def apply_move(self, state, player, move):
        return {"k": state["k"] + 1}

    def terminal(self, state):
        return Terminal(True, None, "d") if state["k"] >= 2 else Terminal(False, None, "")

    def render(self, state):
        return "x"


def test_hostile_name_property_does_not_crash() -> None:
    report = check_game(_NamePropertyRaises(), playouts=2, seed=1)  # must not raise
    assert _status(report, "name") == FAIL
    assert report.game_name is None
    assert not report.ok


class _HostileTerminal:
    def __init__(self, is_terminal: bool) -> None:
        self._it = is_terminal

    @property
    def is_terminal(self):
        return self._it

    @property
    def winner(self):
        return None

    @property
    def reason(self):
        raise RuntimeError("reason boom")  # attribute access on the verdict raises


class _TerminalAttrRaises(_Counter):
    name = "term_attr"

    def terminal(self, state):
        return _HostileTerminal(state["k"] >= self.n)


def test_terminal_with_raising_attribute_does_not_crash() -> None:
    # terminal() returns (does not raise) a verdict whose attribute access raises.
    report = check_game(_TerminalAttrRaises(), playouts=2, seed=1)  # must not raise
    assert _status(report, "terminal") == FAIL
    assert not report.ok


class _BrokenInit:
    name = "broken"

    def initial_state(self):
        raise RuntimeError("nope")


def test_unconstructable_initial_state_short_circuits() -> None:
    report = check_game(_BrokenInit(), playouts=2, seed=1)
    assert _status(report, "initial_state") == FAIL
    assert report.playouts == 0  # nothing ran past the missing initial state
    assert not report.ok


# ---------------------------------------------------------------------------
# Warnings, determinism, formatting
# ---------------------------------------------------------------------------


class _CoinGame:
    """Each ply the mover may 'end' (win) or 'loop'; under a small turn cap some
    random games end and some hit the cap -> a WARN, not a FAIL."""

    name = "coin"

    def initial_state(self):
        return {"k": 0}

    def legal_moves(self, state, player):
        return ["end", "loop"]

    def apply_move(self, state, player, move):
        if move == "end":
            return {"k": state["k"], "winner": player, "over": True}
        return {"k": state["k"] + 1}

    def terminal(self, state):
        if state.get("over"):
            return Terminal(is_terminal=True, winner=state["winner"], reason="ended")
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state):
        return "x"


def test_partial_timeout_is_a_warning_not_a_failure() -> None:
    report = check_game(_CoinGame(), playouts=30, max_turns=4, seed=0)
    assert _status(report, "playout/terminates") == WARN
    assert report.ok  # a warning does not fail the check
    assert report.endings.get("max_turns", 0) > 0
    assert report.endings.get("max_turns", 0) < report.playouts


def test_check_is_deterministic_under_seed() -> None:
    a = check_game(TicTacToe(), playouts=15, seed=42)
    b = check_game(TicTacToe(), playouts=15, seed=42)
    assert a.endings == b.endings
    assert [(c.name, c.status) for c in a.checks] == [(c.name, c.status) for c in b.checks]


def test_format_report_pass_and_fail() -> None:
    good = format_report(check_game(_Counter(), playouts=3, seed=1))
    assert "verdict: PASS" in good
    assert "[PASS] name" in good

    bad = format_report(check_game(_MutatingApply(), playouts=3, seed=1))
    assert "verdict: FAIL" in bad
    assert "[FAIL] apply_move" in bad


def test_format_report_notes_warning_count() -> None:
    text = format_report(check_game(_CoinGame(), playouts=30, max_turns=4, seed=0))
    assert "verdict: PASS (1 warning(s))" in text


# ---------------------------------------------------------------------------
# CLI integration + exit codes
# ---------------------------------------------------------------------------


def test_cli_check_game_passes(capsys) -> None:
    rc = main(["check-game", "tictactoe", "--seed", "1", "--playouts", "5"])
    assert rc == 0
    assert "verdict: PASS" in capsys.readouterr().out


def test_cli_check_game_fails_on_broken_game(tmp_path, capsys) -> None:
    game_file = tmp_path / "broken_game.py"
    game_file.write_text(
        "from ai_arena.game import Terminal\n"
        "class Broken:\n"
        "    name = 'broken'\n"
        "    def initial_state(self): return {'k': 0}\n"
        "    def legal_moves(self, s, p): return [] if s['k'] >= 2 else ['inc']\n"
        "    def apply_move(self, s, p, m):\n"
        "        s['k'] += 1\n"  # mutates the input
        "        return s\n"
        "    def terminal(self, s):\n"
        "        return Terminal(True, None, 'd') if s['k'] >= 2 else Terminal(False, None, '')\n"
        "    def render(self, s): return 'x'\n",
        encoding="utf-8",
    )
    rc = main(["check-game", f"{game_file}:Broken", "--seed", "1", "--playouts", "3"])
    assert rc == 1
    assert "[FAIL] apply_move" in capsys.readouterr().out


def test_cli_check_game_unloadable_spec_errors(capsys) -> None:
    rc = main(["check-game", "/no/such/file.py:Nope"])
    assert rc == 2
    assert "could not load game" in capsys.readouterr().err

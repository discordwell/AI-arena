from __future__ import annotations

from pathlib import Path

import pytest

from ai_arena.tournament import Competitor, run_tournament


def _run(competitors: list[Competitor], **kw):
    return run_tournament(
        competitors=competitors,
        neutral_game=kw.get("neutral_game", "tictactoe"),
        rounds=kw.get("rounds", 1),
        swap_starts=kw.get("swap_starts", False),
        prime_pause=False,
        log_dir=None,
    )


def _points_consistent_with(matches) -> tuple[int, int]:
    wins = sum(1 for m in matches if m.winner is not None)
    draws = len(matches) - wins
    return 3 * wins + 2 * draws, wins


def test_two_competitor_round_robin_completes() -> None:
    comps = [
        Competitor(id="a", home_game="tictactoe", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    res = _run(comps)
    # One pairing, three contexts: a-home, b-home, neutral fallback.
    assert len(res.matches) == 3
    assert {m.context for m in res.matches} == {"home:a", "home:b", "neutral"}
    assert all(m.reason in {"win", "draw"} for m in res.matches)

    expected_points, _ = _points_consistent_with(res.matches)
    total_points = sum(row["points"] for row in res.scoreboard.values())
    assert total_points == expected_points


def test_broken_game_constructor_voids_match_but_tournament_continues(tmp_path: Path) -> None:
    broken = tmp_path / "broken_game.py"
    broken.write_text(
        "class BrokenGame:\n"
        "    def __init__(self):\n"
        "        raise RuntimeError('home game cannot even be constructed')\n",
        encoding="utf-8",
    )
    comps = [
        Competitor(id="a", home_game=str(broken) + ":BrokenGame", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    res = _run(comps)
    assert len(res.matches) == 3

    void = [m for m in res.matches if m.reason.startswith("match_error:")]
    assert len(void) == 1
    assert void[0].context == "home:a"
    assert void[0].winner is None
    assert "RuntimeError" in void[0].reason
    # The game never constructed, so the summary falls back to the spec label.
    assert void[0].game.endswith(":BrokenGame")

    played = [m for m in res.matches if not m.reason.startswith("match_error:")]
    assert len(played) == 2

    # Voided matches award no points (unlike a draw, which awards 1 each).
    expected_points, _ = _points_consistent_with(played)
    total_points = sum(row["points"] for row in res.scoreboard.values())
    assert total_points == expected_points


def test_crashing_apply_move_voids_match(tmp_path: Path) -> None:
    crash = tmp_path / "crash_game.py"
    crash.write_text(
        "from ai_arena.game import Terminal\n"
        "\n"
        "class CrashGame:\n"
        "    name = 'crash'\n"
        "    def initial_state(self):\n"
        "        return {}\n"
        "    def legal_moves(self, state, player):\n"
        "        return [0]\n"
        "    def apply_move(self, state, player, move):\n"
        "        raise RuntimeError('boom mid-match')\n"
        "    def terminal(self, state):\n"
        "        return Terminal(is_terminal=False, winner=None, reason='')\n"
        "    def render(self, state):\n"
        "        return ''\n",
        encoding="utf-8",
    )
    comps = [
        Competitor(id="a", home_game=str(crash) + ":CrashGame", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    res = _run(comps)
    assert len(res.matches) == 3

    void = [m for m in res.matches if m.reason.startswith("match_error:")]
    assert len(void) == 1
    assert void[0].context == "home:a"
    assert "boom mid-match" in void[0].reason
    # The game WAS constructed before the crash, so its real name is recorded.
    assert void[0].game == "crash"

    played = [m for m in res.matches if not m.reason.startswith("match_error:")]
    assert all(m.reason in {"win", "draw"} for m in played)


def test_agent_spawn_failure_forfeits_to_opponent(tmp_path: Path) -> None:
    bad_agent = tmp_path / "bad_agent.py"
    bad_agent.write_text(
        "class BadAgent:\n"
        "    name = 'bad'\n"
        "    def __init__(self):\n"
        "        raise RuntimeError('no api key')\n",
        encoding="utf-8",
    )
    comps = [
        Competitor(id="a", home_game="tictactoe", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent=str(bad_agent) + ":BadAgent"),
    ]
    res = _run(comps)
    assert len(res.matches) == 3
    assert all(m.reason.startswith("agent_spawn_failed:") for m in res.matches)
    assert all(m.winner == "a" for m in res.matches)
    assert res.scoreboard["a"]["wins"] == 3
    assert res.scoreboard["a"]["points"] == 9
    assert res.scoreboard["b"]["losses"] == 3
    assert res.scoreboard["b"]["points"] == 0


def test_bad_spec_still_fails_fast_before_any_match(tmp_path: Path) -> None:
    # Config typos (unloadable specs) should abort at startup, not midway
    # through an expensive tournament.
    comps = [
        Competitor(id="a", home_game=str(tmp_path / "missing.py") + ":Nope", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    with pytest.raises(FileNotFoundError):
        _run(comps)

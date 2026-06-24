from __future__ import annotations

import json
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
        out_path=kw.get("out_path"),
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


def test_results_file_written_after_every_match(tmp_path: Path) -> None:
    # Tournament runs are expensive; the results file must be written as the
    # run progresses (complete=false), not only at the end, so a crash or
    # Ctrl-C keeps the scoreboard so far. A spy home game observes the file
    # at construction time, i.e. at the start of each of its matches.
    out = tmp_path / "results.json"
    obs = tmp_path / "observations.jsonl"
    spy = tmp_path / "spy_game.py"
    spy.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "from ai_arena.games.tictactoe import TicTacToe\n"
        "\n"
        f"OUT = Path({str(out)!r})\n"
        f"OBS = Path({str(obs)!r})\n"
        "\n"
        "class SpyGame(TicTacToe):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        if OUT.exists():\n"
        "            data = json.loads(OUT.read_text(encoding='utf-8'))\n"
        "            entry = {'exists': True, 'matches': len(data['matches']),\n"
        "                     'complete': data['complete']}\n"
        "        else:\n"
        "            entry = {'exists': False}\n"
        "        with OBS.open('a', encoding='utf-8') as f:\n"
        "            f.write(json.dumps(entry) + '\\n')\n",
        encoding="utf-8",
    )

    spy_spec = str(spy) + ":SpyGame"
    comps = [
        Competitor(id="a", home_game=spy_spec, agent="random"),
        Competitor(id="b", home_game=spy_spec, agent="random"),
    ]
    res = _run(comps, out_path=out)

    # Match order: home:a (spy), home:b (spy), neutral (tictactoe).
    entries = [json.loads(line) for line in obs.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2
    # The fence write lands before any match, so match 1 already sees the file.
    assert entries[0] == {"exists": True, "matches": 0, "complete": False}
    assert entries[1] == {"exists": True, "matches": 1, "complete": False}

    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["complete"] is True
    assert len(final["matches"]) == 3
    assert final["scoreboard"] == res.scoreboard
    assert list(out.parent.glob("*.tmp")) == []


def test_results_write_failure_does_not_abort_run(tmp_path: Path, capsys) -> None:
    # The results file is a convenience summary; failing to write it must not
    # cancel the remaining (expensive) matches.
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    out = blocker / "results.json"  # parent is a file -> every write fails

    comps = [
        Competitor(id="a", home_game="tictactoe", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    res = _run(comps, out_path=out)

    assert len(res.matches) == 3  # the run completed despite the write failures
    assert all(m.reason in {"win", "draw"} for m in res.matches)
    assert "failed to write results" in capsys.readouterr().err


def test_bad_spec_still_fails_fast_before_any_match(tmp_path: Path) -> None:
    # Config typos (unloadable specs) should abort at startup, not midway
    # through an expensive tournament — and must not touch a previous run's
    # results file (the fence write happens only after validation).
    prior = '{"complete": true, "matches": ["precious"]}'
    out = tmp_path / "results.json"
    out.write_text(prior, encoding="utf-8")

    comps = [
        Competitor(id="a", home_game=str(tmp_path / "missing.py") + ":Nope", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    with pytest.raises(FileNotFoundError):
        _run(comps, out_path=out)

    assert out.read_text(encoding="utf-8") == prior


def test_tuned_agent_spec_runs_as_competitor() -> None:
    # A parametrized built-in spec is usable as a competitor's agent. Full-depth
    # `search` never loses tic-tac-toe, so the depth-1 competitor cannot out-point
    # it however the (unseeded) tie-breaks fall.
    comps = [
        Competitor(id="strong", home_game="tictactoe", agent="search"),
        Competitor(id="weak", home_game="tictactoe", agent="search:max_depth=1"),
    ]
    res = _run(comps)
    assert res.complete
    assert res.scoreboard["strong"]["losses"] == 0
    assert res.scoreboard["strong"]["points"] >= res.scoreboard["weak"]["points"]


def test_bad_agent_param_fails_fast_without_clobbering_out(tmp_path: Path) -> None:
    # A bad agent *parameter* fails fast at up-front spec resolution, exactly like
    # an unloadable spec — before the results file is fenced, so a prior run's
    # file is left untouched.
    prior = '{"complete": true, "matches": ["precious"]}'
    out = tmp_path / "results.json"
    out.write_text(prior, encoding="utf-8")

    comps = [
        Competitor(id="a", home_game="tictactoe", agent="search:max_depth=notint"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    with pytest.raises(ValueError, match="must be int"):
        _run(comps, out_path=out)

    assert out.read_text(encoding="utf-8") == prior

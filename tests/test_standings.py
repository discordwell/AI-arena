from __future__ import annotations

from ai_arena.tournament import (
    Competitor,
    MatchSummary,
    compute_standings,
    format_standings,
    parse_match_summaries,
    run_tournament,
)


def _m(
    context: str,
    p0: str,
    p1: str,
    winner: str | None,
    *,
    reason: str = "win",
    game: str = "g",
    turns: int = 1,
) -> MatchSummary:
    return MatchSummary(context=context, game=game, p0=p0, p1=p1, winner=winner, reason=reason, turns=turns)


def test_compute_standings_scores_points_and_ranks_by_points() -> None:
    matches = [
        _m("home:a", "a", "b", "a"),  # a beats b
        _m("home:b", "b", "a", None, reason="draw"),  # draw
        _m("neutral", "a", "c", "a"),  # a beats c
        _m("home:c", "c", "b", "c"),  # c beats b
    ]
    report = compute_standings(matches)

    by_id = {r.cid: r for r in report.rows}
    # win=3, draw=1, loss=0 (the live tournament's rule, reused here).
    assert (by_id["a"].points, by_id["a"].wins, by_id["a"].draws, by_id["a"].played) == (7, 2, 1, 3)
    assert (by_id["c"].points, by_id["c"].wins, by_id["c"].losses) == (3, 1, 1)
    assert (by_id["b"].points, by_id["b"].draws, by_id["b"].losses) == (1, 1, 2)

    # Ranked: most points first, ties broken by id.
    assert [r.cid for r in report.rows] == ["a", "c", "b"]
    assert (report.scored, report.voided) == (4, 0)
    assert report.reasons == {"win": 3, "draw": 1}


def test_compute_standings_head_to_head_uses_sorted_pair_view() -> None:
    matches = [
        _m("home:x", "x", "y", "x"),  # x wins
        _m("home:y", "y", "x", "y"),  # y wins
        _m("neutral", "x", "y", None, reason="draw"),  # draw
    ]
    report = compute_standings(matches)

    assert len(report.head_to_head) == 1
    h = report.head_to_head[0]
    assert (h.a, h.b) == ("x", "y")  # tallied from the smaller id's perspective
    assert (h.a_wins, h.draws, h.b_wins) == (1, 1, 1)


def test_compute_standings_home_away_split() -> None:
    matches = [
        _m("home:a", "a", "b", "a"),  # a at home (win); b away (loss)
        _m("away:c", "a", "b", "b"),  # both away (a loss, b win)
        _m("neutral", "b", "a", None, reason="draw"),  # both away (draw)
    ]
    report = compute_standings(matches)
    cs = report.context_splits

    # Only context "home:<cid>" counts as that competitor's home game.
    assert cs["a"]["home"] == (1, 0, 0)
    assert cs["a"]["away"] == (0, 1, 1)
    assert cs["b"]["home"] == (0, 0, 0)  # b never played its own home game here
    assert cs["b"]["away"] == (1, 1, 1)


def test_compute_standings_excludes_voided_matches() -> None:
    matches = [
        _m("home:a", "a", "b", "a"),  # a beats b (scored)
        _m("home:c", "c", "b", None, reason="match_error:RuntimeError: boom"),  # void: c vs b
    ]
    report = compute_standings(matches)

    assert (report.scored, report.voided) == (1, 1)
    by_id = {r.cid: r for r in report.rows}
    assert (by_id["a"].points, by_id["a"].played) == (3, 1)
    # c appears only in the void: it awards nothing and is not a "played" game,
    # and a void is NOT a draw (which would have given c a point).
    assert (by_id["c"].points, by_id["c"].played, by_id["c"].draws) == (0, 0, 0)
    # b's real loss still counts; the void must not bump its played count or
    # hand it a draw.
    assert (by_id["b"].points, by_id["b"].played, by_id["b"].draws) == (0, 1, 0)
    assert "match_error:RuntimeError: boom" not in report.reasons


def test_parse_match_summaries_is_defensive() -> None:
    payload = {
        "matches": [
            {"context": "home:a", "p0": "a", "p1": "b", "winner": "a", "reason": "win", "turns": 3},
            "not-a-dict",  # skipped
            {"p0": "a"},  # missing p1 -> skipped (cannot attribute)
            {"p0": 1, "p1": "b"},  # non-str id -> skipped
            {"p0": "a", "p1": "b"},  # missing winner/reason/turns -> tolerated with defaults
            {"p0": "a", "p1": "b", "winner": "zzz", "reason": "win"},  # bogus winner -> coerced to None
        ]
    }
    out = parse_match_summaries(payload)

    assert len(out) == 3
    assert (out[0].winner, out[0].turns) == ("a", 3)
    assert (out[1].winner, out[1].reason, out[1].turns) == (None, "", 0)
    # A winner that is neither contestant is dropped so the scorer can't KeyError.
    assert out[2].winner is None

    # No matches / wrong type -> empty, never raises.
    assert parse_match_summaries({}) == []
    assert parse_match_summaries({"matches": "nope"}) == []


def test_compute_standings_survives_a_bogus_winner_in_the_file() -> None:
    # A hand-edited / corrupt results file whose winner is neither contestant
    # must still summarize (the bogus result counts as a no-decision), not crash
    # the scorer with a KeyError.
    matches = parse_match_summaries(
        {"matches": [{"context": "neutral", "p0": "a", "p1": "b", "winner": "ghost", "reason": "win"}]}
    )
    report = compute_standings(matches)
    assert {r.cid for r in report.rows} == {"a", "b"}
    assert all(r.points == 1 for r in report.rows)  # treated as a draw-equivalent no-decision


def test_format_standings_flags_incomplete_and_empty() -> None:
    report = compute_standings([])
    assert report.rows == []

    text = format_standings(report, complete=False)
    assert "[INCOMPLETE run]" in text
    assert "(no matches recorded)" in text


def test_format_standings_sections_and_optional_blocks() -> None:
    matches = [
        _m("home:a", "a", "b", "a", reason="win"),
        _m("neutral", "a", "b", None, reason="draw"),
    ]
    report = compute_standings(matches)

    base = format_standings(report)
    assert "head-to-head" in base
    assert "how games ended" in base
    assert "home vs away" not in base  # off by default
    assert "matches (" not in base  # off by default

    full = format_standings(report, show_context=True, matches=matches)
    assert "home vs away" in full
    assert "matches (2):" in full
    assert "VOID" not in full  # no voided matches here


def test_recomputed_standings_match_live_tournament_scoreboard() -> None:
    # The post-hoc reader must agree with the live scorer: recomputing the
    # scoreboard from the recorded matches reproduces what the tournament wrote.
    comps = [
        Competitor(id="a", home_game="tictactoe", agent="random"),
        Competitor(id="b", home_game="tictactoe", agent="random"),
    ]
    res = run_tournament(
        competitors=comps,
        neutral_game="tictactoe",
        rounds=1,
        swap_starts=True,
        prime_pause=False,
        log_dir=None,
    )

    report = compute_standings(res.matches)
    recomputed = {
        r.cid: {"wins": r.wins, "losses": r.losses, "draws": r.draws, "points": r.points}
        for r in report.rows
    }
    assert recomputed == res.scoreboard

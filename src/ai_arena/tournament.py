from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .engine import MatchResult, atomic_write_json, play_match
from .specs import resolve_agent_factory
from .specs import resolve_game_factory as _game_factory  # re-exported: benchmark/check/tests import it here


@dataclass(frozen=True, slots=True)
class Competitor:
    id: str
    home_game: str
    agent: str


@dataclass(frozen=True, slots=True)
class MatchSummary:
    context: str
    game: str
    p0: str
    p1: str
    winner: str | None
    reason: str
    turns: int


@dataclass(frozen=True, slots=True)
class TournamentResult:
    started_ts_ms: int
    duration_ms: int
    complete: bool  # False in mid-run snapshots written to the results file
    matches: list[MatchSummary]
    scoreboard: dict[str, dict[str, int]]


def _agent_factory(spec: str) -> Callable[[], Any]:
    """
    Resolve a competitor's agent spec to a zero-arg factory (a fresh agent per
    match). Built-in tunable params are validated eagerly (config typos fail
    fast, like a bad spec). The tournament deliberately leaves built-ins unseeded
    -- varied play across rounds -- so it threads ``None`` through the shared
    seed-aware resolver.
    """
    if spec == "human":
        from .agents.human import HumanAgent

        return HumanAgent
    make = resolve_agent_factory(spec)
    return lambda: make(None)


def _pairings(xs: list[Competitor]) -> list[tuple[Competitor, Competitor]]:
    out: list[tuple[Competitor, Competitor]] = []
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            out.append((xs[i], xs[j]))
    return out


def _scoreboard_init(competitors: list[Competitor]) -> dict[str, dict[str, int]]:
    return {c.id: {"wins": 0, "losses": 0, "draws": 0, "points": 0} for c in competitors}


def _apply_result(sb: dict[str, dict[str, int]], p0: str, p1: str, winner: str | None) -> None:
    if winner is None:
        sb[p0]["draws"] += 1
        sb[p1]["draws"] += 1
        sb[p0]["points"] += 1
        sb[p1]["points"] += 1
        return

    loser = p1 if winner == p0 else p0
    sb[winner]["wins"] += 1
    sb[loser]["losses"] += 1
    sb[winner]["points"] += 3


def _maybe_close(agent: Any) -> None:
    close = getattr(agent, "close", None)
    if callable(close):
        close()


@dataclass(frozen=True, slots=True)
class SeatOutcome:
    winner_id: str | None
    reason: str
    turns: int
    game: str
    scored: bool  # False = voided by a game/harness crash; no points awarded


def _play_seat(
    *,
    game_factory: Callable[[], Any],
    game_label: str,
    p0_id: str,
    p1_id: str,
    agent_factories: dict[str, Callable[[], Any]],
    prime_pause: bool,
    log_path: Path | None,
) -> SeatOutcome:
    """
    Run one seated match without letting a buggy game or agent kill the whole
    tournament (matches are expensive: every move can be an LLM call).

    An agent that fails to start forfeits the match (the opponent wins, like a
    timeout). A crash in the game code voids the match: it is recorded with
    reason "match_error:..." but awards no points.
    """
    agent0: Any = None
    agent1: Any = None
    game_name = game_label  # refined to game.name once the game is constructed
    try:
        game = game_factory()
        game_name = str(getattr(game, "name", game_label))

        try:
            agent0 = agent_factories[p0_id]()
        except Exception as e:
            return SeatOutcome(p1_id, f"agent_spawn_failed:{type(e).__name__}", 0, game_name, True)
        try:
            agent1 = agent_factories[p1_id]()
        except Exception as e:
            return SeatOutcome(p0_id, f"agent_spawn_failed:{type(e).__name__}", 0, game_name, True)

        res: MatchResult = play_match(game, agent0, agent1, prime_pause=prime_pause, log_path=log_path)
        winner_id = None if res.winner is None else (p0_id if res.winner == 0 else p1_id)
        return SeatOutcome(winner_id, res.reason, res.turns, res.game, True)
    except Exception as e:
        detail = " ".join(str(e).split())[:200]
        return SeatOutcome(None, f"match_error:{type(e).__name__}:{detail}", 0, game_name, False)
    finally:
        for agent in (agent0, agent1):
            try:
                _maybe_close(agent)
            except Exception:
                pass


def run_tournament(
    *,
    competitors: list[Competitor],
    neutral_game: str,
    rounds: int,
    swap_starts: bool,
    prime_pause: bool,
    log_dir: Path | None,
    out_path: Path | None = None,
) -> TournamentResult:
    started = time.time()
    started_ts_ms = int(started * 1000)

    sb = _scoreboard_init(competitors)
    matches: list[MatchSummary] = []

    def snapshot(complete: bool) -> TournamentResult:
        return TournamentResult(
            started_ts_ms=started_ts_ms,
            duration_ms=int((time.time() - started) * 1000),
            complete=complete,
            matches=matches,
            scoreboard=sb,
        )

    def write_results(result: TournamentResult) -> None:
        # Best-effort: the results file is a convenience summary (matches are
        # also in per-match logs and on stdout); a failed write must never
        # abort the remaining, expensive matches.
        if not out_path:
            return
        try:
            atomic_write_json(out_path, asdict(result))
        except Exception as e:
            print(f"warning: failed to write results to {out_path} ({type(e).__name__}: {e})", file=sys.stderr)

    # Resolve every spec up front so config typos fail fast, before any
    # (potentially expensive) match runs.
    game_factories = {c.id: _game_factory(c.home_game) for c in competitors}
    game_labels = {c.id: c.home_game for c in competitors}
    agent_factories = {c.id: _agent_factory(c.agent) for c in competitors}
    neutral_factory = _game_factory(neutral_game)

    # Fence the results file only after fail-fast validation passed (a config
    # typo must not clobber a previous run's results): a leftover file from a
    # previous run with the same --out would otherwise pose as this run's live
    # data until the first match finishes (hours, with LLM agents).
    write_results(snapshot(complete=False))

    for a, b in _pairings(competitors):
        # Matches per pairing: a-home, b-home, + third competitor's home (or neutral fallback).
        others = [c for c in competitors if c.id not in (a.id, b.id)]
        third_p0 = min(a.id, b.id)
        if others:
            third = others[0]
            third_scenario = ("away:" + third.id, game_factories[third.id], game_labels[third.id], third_p0)
        else:
            third_scenario = ("neutral", neutral_factory, neutral_game, third_p0)

        scenarios = [
            ("home:" + a.id, game_factories[a.id], game_labels[a.id], a.id),
            ("home:" + b.id, game_factories[b.id], game_labels[b.id], b.id),
            third_scenario,
        ]

        for context, game_factory, game_label, p0_default in scenarios:
            for r in range(rounds):
                seats = [(p0_default, a.id if p0_default == b.id else b.id)]
                if swap_starts:
                    seats.append((seats[0][1], seats[0][0]))

                for p0_id, p1_id in seats:
                    log_path = None
                    if log_dir:
                        safe_ctx = context.replace(":", "_")
                        log_path = (log_dir / f"{a.id}_vs_{b.id}" / f"{safe_ctx}_r{r}_{p0_id}_starts.json")

                    outcome = _play_seat(
                        game_factory=game_factory,
                        game_label=game_label,
                        p0_id=p0_id,
                        p1_id=p1_id,
                        agent_factories=agent_factories,
                        prime_pause=prime_pause,
                        log_path=log_path,
                    )

                    summary = MatchSummary(
                        context=context,
                        game=outcome.game,
                        p0=p0_id,
                        p1=p1_id,
                        winner=outcome.winner_id,
                        reason=outcome.reason,
                        turns=outcome.turns,
                    )
                    matches.append(summary)
                    if outcome.scored:
                        _apply_result(sb, p0_id, p1_id, outcome.winner_id)

                    # Tournaments are expensive (every move can be an LLM
                    # call); rewrite the results file after each match so a
                    # crashed or interrupted run keeps its scoreboard.
                    write_results(snapshot(complete=False))

                    # Live match result output
                    w = outcome.winner_id or ("DRAW" if outcome.scored else "VOID")
                    print(
                        f"  match {len(matches):>2}: [{context}] {p0_id} vs {p1_id}"
                        f"  ->  {w} ({outcome.reason}, {outcome.turns}t)",
                        flush=True,
                    )

    result = snapshot(complete=True)
    write_results(result)
    return result


# ---------------------------------------------------------------------------
# Standings report
#
# A tournament `results.json` is durable (rule 3), but until now it could only
# be understood from the live stdout scoreboard at run time. These functions
# read a results file back into a ranked leaderboard, a head-to-head record, a
# home/away split, and a termination-reason histogram -- the post-hoc reader for
# tournament artifacts, mirroring what `replay` does for match logs. The report
# is derived from the recorded `matches` list using the same scoring rule as the
# live run (`_apply_result`), so it is self-consistent and works on partial or
# older files that predate the `complete`/`scoreboard` fields.
# ---------------------------------------------------------------------------

# A match is voided (no points awarded) only when game/harness code crashed; the
# tournament records that as reason "match_error:...". Everything else is scored:
# a real winner, a draw (winner None), or an agent-spawn forfeit (has a winner).
_VOID_PREFIX = "match_error"


def _is_void(reason: str) -> bool:
    return reason.startswith(_VOID_PREFIX)


@dataclass(frozen=True, slots=True)
class StandingsRow:
    cid: str
    points: int
    wins: int
    losses: int
    draws: int
    played: int  # scored matches this competitor appeared in


@dataclass(frozen=True, slots=True)
class HeadToHead:
    a: str  # the lexicographically smaller competitor id
    b: str
    a_wins: int
    draws: int
    b_wins: int


@dataclass(frozen=True, slots=True)
class StandingsReport:
    rows: list[StandingsRow]  # ranked: most points first, ties broken by id
    head_to_head: list[HeadToHead]  # one per pairing, sorted by (a, b)
    context_splits: dict[str, dict[str, tuple[int, int, int]]]  # cid -> {"home"/"away": (w, d, l)}
    reasons: dict[str, int]  # terminal reason -> count (scored matches only)
    scored: int
    voided: int


def parse_match_summaries(payload: dict[str, Any]) -> list[MatchSummary]:
    """
    Pull the recorded matches out of a results payload, defensively.

    A match with no usable contestant ids is skipped (it cannot be attributed);
    other fields fall back to sensible defaults so a partial or hand-edited file
    still summarizes instead of raising.
    """
    raw = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[MatchSummary] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        p0, p1 = m.get("p0"), m.get("p1")
        if not isinstance(p0, str) or not isinstance(p1, str):
            continue
        # A winner must be one of the two contestants. Anything else -- None for a
        # draw, or a corrupt/hand-edited value -- becomes "no winner", so the
        # scorer (which only knows the two seat ids) cannot KeyError on it.
        winner = m.get("winner")
        turns = m.get("turns")
        out.append(
            MatchSummary(
                context=str(m.get("context", "")),
                game=str(m.get("game", "")),
                p0=p0,
                p1=p1,
                winner=winner if winner in (p0, p1) else None,
                reason=str(m.get("reason", "")),
                turns=turns if isinstance(turns, int) else 0,
            )
        )
    return out


def compute_standings(matches: list[MatchSummary]) -> StandingsReport:
    """Recompute the full standings report from a list of match summaries."""
    ids = sorted({m.p0 for m in matches} | {m.p1 for m in matches})
    sb = {cid: {"wins": 0, "losses": 0, "draws": 0, "points": 0} for cid in ids}
    played = {cid: 0 for cid in ids}
    # head-to-head keyed by the sorted pair; tallied from the smaller id's view.
    h2h: dict[tuple[str, str], list[int]] = {}
    # per-competitor home/away record as mutable [wins, draws, losses].
    ctx: dict[str, dict[str, list[int]]] = {cid: {"home": [0, 0, 0], "away": [0, 0, 0]} for cid in ids}
    reasons: dict[str, int] = {}
    scored = voided = 0

    for m in matches:
        if _is_void(m.reason):
            voided += 1
            continue
        scored += 1
        reasons[m.reason] = reasons.get(m.reason, 0) + 1
        _apply_result(sb, m.p0, m.p1, m.winner)
        played[m.p0] += 1
        played[m.p1] += 1

        a, b = (m.p0, m.p1) if m.p0 <= m.p1 else (m.p1, m.p0)
        rec = h2h.setdefault((a, b), [0, 0, 0])
        if m.winner is None:
            rec[1] += 1
        elif m.winner == a:
            rec[0] += 1
        else:
            rec[2] += 1

        # "home:<cid>" marks <cid>'s own home game; every other context (a rival's
        # away game, or the neutral game) counts as away for both contestants.
        for cid in (m.p0, m.p1):
            wdl = ctx[cid]["home" if m.context == f"home:{cid}" else "away"]
            if m.winner is None:
                wdl[1] += 1
            elif m.winner == cid:
                wdl[0] += 1
            else:
                wdl[2] += 1

    rows = [
        StandingsRow(cid, sb[cid]["points"], sb[cid]["wins"], sb[cid]["losses"], sb[cid]["draws"], played[cid])
        for cid in ids
    ]
    rows.sort(key=lambda r: (-r.points, r.cid))

    head_to_head = [HeadToHead(a, b, rec[0], rec[1], rec[2]) for (a, b), rec in sorted(h2h.items())]
    context_splits = {
        cid: {"home": tuple(ctx[cid]["home"]), "away": tuple(ctx[cid]["away"])} for cid in ids
    }
    return StandingsReport(
        rows=rows,
        head_to_head=head_to_head,
        context_splits=context_splits,
        reasons=reasons,
        scored=scored,
        voided=voided,
    )


def format_standings(
    report: StandingsReport,
    *,
    complete: bool | None = None,
    show_context: bool = False,
    matches: list[MatchSummary] | None = None,
) -> str:
    """
    Render a :class:`StandingsReport` as text.

    ``complete`` (the results file's flag, if any) flags an interrupted run;
    ``show_context`` adds each competitor's home/away split; passing ``matches``
    appends the full match list (otherwise it is omitted).
    """
    total = report.scored + report.voided
    header = f"standings ({report.scored} scored"
    if report.voided:
        header += f", {report.voided} voided"
    header += f" / {total} matches)"
    if complete is False:
        header += "  [INCOMPLETE run]"
    lines = [header]

    if not report.rows:
        lines.append("(no matches recorded)")
        return "\n".join(lines)

    # Leaderboard.
    name_w = max(len("competitor"), max(len(r.cid) for r in report.rows))
    lines.append("")
    lines.append(f"  {'#':>2}  {'competitor':<{name_w}}  {'pts':>3}  {'W':>2} {'L':>2} {'D':>2}  {'played':>6}")
    for i, r in enumerate(report.rows, 1):
        lines.append(
            f"  {i:>2}  {r.cid:<{name_w}}  {r.points:>3}  "
            f"{r.wins:>2} {r.losses:>2} {r.draws:>2}  {r.played:>6}"
        )

    # Head-to-head.
    if report.head_to_head:
        pair_w = max(len(f"{h.a} vs {h.b}") for h in report.head_to_head)
        lines.append("")
        lines.append("head-to-head (W-D-L, first contestant's view):")
        for h in report.head_to_head:
            lines.append(f"  {f'{h.a} vs {h.b}':<{pair_w}}  {h.a_wins}-{h.draws}-{h.b_wins}")

    # Home vs away split (optional).
    if show_context:
        lines.append("")
        lines.append("home vs away (W-D-L):")
        for r in report.rows:
            home = report.context_splits[r.cid]["home"]
            away = report.context_splits[r.cid]["away"]
            lines.append(
                f"  {r.cid:<{name_w}}  home {home[0]}-{home[1]}-{home[2]}   away {away[0]}-{away[1]}-{away[2]}"
            )

    # Termination reasons.
    if report.reasons:
        reason_w = max(len(k) for k in report.reasons)
        lines.append("")
        lines.append("how games ended:")
        for reason, n in sorted(report.reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {reason:<{reason_w}}  {n}")

    # Full match list (optional).
    if matches is not None:
        lines.append("")
        lines.append(f"matches ({len(matches)}):")
        for i, m in enumerate(matches, 1):
            outcome = m.winner if m.winner is not None else ("VOID" if _is_void(m.reason) else "draw")
            lines.append(
                f"  {i:>2}. [{m.context}] {m.p0} vs {m.p1}  ->  {outcome} ({m.reason}, {m.turns}t)"
            )

    return "\n".join(lines)


def _load_config(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config must be a TOML table")
    return data


def cmd_tournament(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    cfg = _load_config(config_path)

    competitors_raw = cfg.get("competitors", [])
    if not isinstance(competitors_raw, list) or not competitors_raw:
        raise ValueError("Config must contain [[competitors]] entries")

    competitors: list[Competitor] = []
    for c in competitors_raw:
        if not isinstance(c, dict):
            raise ValueError("Each [[competitors]] entry must be a table")
        competitors.append(
            Competitor(
                id=str(c["id"]),
                home_game=str(c.get("home_game", "tictactoe")),
                agent=str(c.get("agent", "random")),
            )
        )

    neutral_game = str(cfg.get("neutral_game", "tictactoe"))
    rounds = int(cfg.get("rounds", 1))
    swap_starts = bool(cfg.get("swap_starts", False))
    prime_pause = bool(cfg.get("prime_pause", False))

    log_dir = None
    if cfg.get("log_dir"):
        log_dir = Path(str(cfg["log_dir"])).expanduser().resolve()

    out_path = Path(args.out).expanduser().resolve() if args.out else None

    result = run_tournament(
        competitors=competitors,
        neutral_game=neutral_game,
        rounds=rounds,
        swap_starts=swap_starts,
        prime_pause=prime_pause,
        log_dir=log_dir,
        out_path=out_path,
    )

    print("scoreboard:")
    for cid, row in sorted(result.scoreboard.items(), key=lambda kv: (-kv[1]["points"], kv[0])):
        print(f"  {cid}: {row}")

    if out_path:
        print(f"out: {out_path}")

    return 0


def load_tournament_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("tournament", help="Run the PvPvP round robin from a TOML config")
    p.add_argument("--config", default="arena.toml", help="Path to config TOML")
    p.add_argument("--out", help="Write JSON results to this path")
    p.set_defaults(func=cmd_tournament)


def cmd_standings(args: argparse.Namespace) -> int:
    path = Path(args.results).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # missing / unreadable / not JSON
        print(f"error: could not read results file {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print(f"error: results file {path} is not a JSON object", file=sys.stderr)
        return 1

    matches = parse_match_summaries(payload)
    report = compute_standings(matches)
    complete = payload.get("complete")
    print(
        format_standings(
            report,
            complete=complete if isinstance(complete, bool) else None,
            show_context=args.by_context,
            matches=matches if args.matches else None,
        )
    )
    return 0


def load_standings_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "standings",
        help="Summarize a tournament results JSON into a leaderboard + head-to-head (headless)",
    )
    p.add_argument("results", help="Path to a tournament results JSON (from `tournament --out`)")
    p.add_argument("--by-context", action="store_true", help="Also show each competitor's home vs away record")
    p.add_argument("--matches", action="store_true", help="List every recorded match")
    p.set_defaults(func=cmd_standings)


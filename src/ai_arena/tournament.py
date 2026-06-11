from __future__ import annotations

import argparse
import shlex
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .engine import MatchResult, atomic_write_json, play_match
from .games.tictactoe import TicTacToe
from .loading import load_symbol


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


def _builtin_game_factory(name: str) -> Callable[[], Any] | None:
    if name == "tictactoe":
        return TicTacToe
    return None


def _game_factory(spec: str) -> Callable[[], Any]:
    builtin = _builtin_game_factory(spec)
    if builtin is not None:
        return builtin
    obj = load_symbol(spec)
    if callable(obj):
        return obj  # type: ignore[return-value]
    return lambda: obj


def _agent_factory(spec: str) -> Callable[[], Any]:
    if spec == "random":
        from .agents.random_agent import RandomAgent

        return RandomAgent
    if spec == "human":
        from .agents.human import HumanAgent

        return HumanAgent
    if spec.startswith("subprocess:"):
        from .agents.subprocess_agent import SubprocessAgent

        cmd = shlex.split(spec.removeprefix("subprocess:").strip())
        if not cmd:
            raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
        return lambda: SubprocessAgent(cmd)

    obj = load_symbol(spec)
    if callable(obj):
        return obj  # type: ignore[return-value]
    return lambda: obj


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


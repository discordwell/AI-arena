from __future__ import annotations

import argparse
import json
import shlex
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .engine import MatchResult, play_match
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


def run_tournament(
    *,
    competitors: list[Competitor],
    neutral_game: str,
    rounds: int,
    swap_starts: bool,
    prime_pause: bool,
    log_dir: Path | None,
) -> TournamentResult:
    started = time.time()
    started_ts_ms = int(started * 1000)

    sb = _scoreboard_init(competitors)
    matches: list[MatchSummary] = []

    neutral_game_factory = _game_factory(neutral_game)

    for a, b in _pairings(competitors):
        # Matches per pairing: a-home, b-home, neutral.
        scenarios = [
            ("home:" + a.id, _game_factory(a.home_game), a.id),
            ("home:" + b.id, _game_factory(b.home_game), b.id),
            ("neutral", neutral_game_factory, min(a.id, b.id)),
        ]

        for context, game_factory, p0_default in scenarios:
            for r in range(rounds):
                seats = [(p0_default, a.id if p0_default == b.id else b.id)]
                if swap_starts:
                    seats.append((seats[0][1], seats[0][0]))

                for p0_id, p1_id in seats:
                    game = game_factory()
                    p0 = a if a.id == p0_id else b
                    p1 = b if p0 is a else a

                    agent0 = _agent_factory(p0.agent)()
                    agent1 = _agent_factory(p1.agent)()

                    log_path = None
                    if log_dir:
                        safe_ctx = context.replace(":", "_")
                        log_path = (log_dir / f"{a.id}_vs_{b.id}" / f"{safe_ctx}_r{r}_{p0_id}_starts.json")

                    try:
                        res: MatchResult = play_match(
                            game,
                            agent0,
                            agent1,
                            prime_pause=prime_pause,
                            log_path=log_path,
                        )
                    finally:
                        _maybe_close(agent0)
                        _maybe_close(agent1)

                    winner_id = None if res.winner is None else (p0_id if res.winner == 0 else p1_id)
                    matches.append(
                        MatchSummary(
                            context=context,
                            game=res.game,
                            p0=p0_id,
                            p1=p1_id,
                            winner=winner_id,
                            reason=res.reason,
                            turns=res.turns,
                        )
                    )
                    _apply_result(sb, p0_id, p1_id, winner_id)

    duration_ms = int((time.time() - started) * 1000)
    return TournamentResult(
        started_ts_ms=started_ts_ms,
        duration_ms=duration_ms,
        matches=matches,
        scoreboard=sb,
    )


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

    result = run_tournament(
        competitors=competitors,
        neutral_game=neutral_game,
        rounds=rounds,
        swap_starts=swap_starts,
        prime_pause=prime_pause,
        log_dir=log_dir,
    )

    print("scoreboard:")
    for cid, row in sorted(result.scoreboard.items(), key=lambda kv: (-kv[1]["points"], kv[0])):
        print(f"  {cid}: {row}")

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"out: {out_path}")

    return 0


def load_tournament_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("tournament", help="Run the PvPvP round robin from a TOML config")
    p.add_argument("--config", default="arena.toml", help="Path to config TOML")
    p.add_argument("--out", help="Write JSON results to this path")
    p.set_defaults(func=cmd_tournament)


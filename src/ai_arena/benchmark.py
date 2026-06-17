from __future__ import annotations

import argparse
import shlex
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .engine import MatchResult, atomic_write_json, play_match
from .loading import load_symbol
from .tournament import _game_factory

# Engine forfeit reasons that mean the *losing* agent failed, rather than losing
# by the game's own rules. Kept in sync with engine.play_match.
FORFEIT_REASONS = frozenset({"timeout", "agent_error", "illegal_move"})

# A factory that builds a fresh agent for one game, given that game's seed (or
# None for nondeterministic play). Each game gets its own agent instance so the
# games are independent and a stateful agent (e.g. a subprocess bot) is not
# replayed across games.
SeededAgentFactory = Callable[[int | None], Any]


def _seeded_agent_factory(spec: str) -> SeededAgentFactory:
    """
    Resolve an agent spec to a per-game factory, threading a per-game seed into
    the built-in seedable agents (``random`` / ``greedy`` / ``search``) so that
    repeated games vary yet the whole benchmark is reproducible under ``--seed``.

    Non-seedable built-ins, subprocess bots, and ``<path>:<symbol>`` agents
    ignore the seed (they have no seed parameter to thread).

    ``human`` is rejected: it blocks on stdin and cannot play an unattended
    benchmark.
    """
    if spec == "human":
        raise ValueError("the 'human' agent cannot be benchmarked (it blocks on stdin)")
    if spec == "random":
        from .agents.random_agent import RandomAgent

        return lambda seed: RandomAgent(seed=seed)
    if spec == "greedy":
        from .agents.greedy import GreedyAgent

        return lambda seed: GreedyAgent(seed=seed)
    if spec == "search":
        from .agents.search import SearchAgent

        return lambda seed: SearchAgent(seed=seed)
    if spec.startswith("subprocess:"):
        from .agents.subprocess_agent import SubprocessAgent

        cmd = shlex.split(spec.removeprefix("subprocess:").strip())
        if not cmd:
            raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
        return lambda seed: SubprocessAgent(cmd)

    obj = load_symbol(spec)
    factory: Callable[[], Any] = obj if callable(obj) else (lambda: obj)
    return lambda seed: factory()


@dataclass(frozen=True, slots=True)
class BenchmarkProgress:
    """Cheap per-game progress snapshot handed to a live-progress callback."""

    completed: int
    total: int
    a_wins: int
    b_wins: int
    draws: int


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    game: str
    a_label: str
    b_label: str
    games: int  # games actually completed (< requested if interrupted)
    a_wins: int
    b_wins: int
    draws: int
    a_forfeits: int  # games A lost via timeout / agent_error / illegal_move
    b_forfeits: int
    reason_counts: dict[str, int]  # terminal reason -> count
    avg_turns: float
    a_avg_ms: float
    a_max_ms: float
    b_avg_ms: float
    b_max_ms: float
    incomplete: bool = False  # True if a KeyboardInterrupt stopped the run early


def _maybe_close(agent: Any) -> None:
    close = getattr(agent, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def run_benchmark(
    *,
    game_factory: Callable[[], Any],
    a_factory: SeededAgentFactory,
    b_factory: SeededAgentFactory,
    a_label: str,
    b_label: str,
    games: int,
    swap_starts: bool = True,
    base_seed: int | None = None,
    max_turns: int = 10_000,
    progress: Callable[[BenchmarkProgress], None] | None = None,
) -> BenchmarkResult:
    """
    Play ``games`` independent matches between contestant A (``a_factory``) and
    contestant B (``b_factory``) on the game built by ``game_factory``, and
    report head-to-head outcomes *by contestant* (not by seat).

    With ``swap_starts`` (the default) the contestants alternate who moves first,
    so first-mover advantage does not bias the result. With ``base_seed`` set,
    every game gets distinct, reproducible seeds (game ``i`` uses ``base_seed +
    2i`` and ``+ 2i + 1`` for the two seats), so the whole benchmark replays
    identically.

    A ``KeyboardInterrupt`` ends the run early and returns the partial result
    with ``incomplete=True`` — useful for long benchmarks of expensive agents.
    """
    if games <= 0:
        raise ValueError("games must be a positive integer")

    # Construct one game up front: surfaces a broken game spec immediately and
    # gives the result a stable name even if zero games complete.
    game_name = str(getattr(game_factory(), "name", "game"))

    a_wins = b_wins = draws = 0
    a_forfeits = b_forfeits = 0
    reason_counts: dict[str, int] = {}
    total_turns = 0
    a_ms: list[float] = []
    b_ms: list[float] = []
    completed = 0
    incomplete = False

    for i in range(games):
        a_is_p0 = (i % 2 == 0) if swap_starts else True
        if base_seed is None:
            seed_a = seed_b = None
        else:
            seed_a = base_seed + 2 * i
            seed_b = base_seed + 2 * i + 1

        # Run one game under containment: a failure here -- Ctrl-C, a flaky
        # subprocess bot that fails to spawn, or a crash in game code -- stops
        # the benchmark but keeps every game completed so far. An expensive run
        # (each game can be many LLM calls) is never discarded wholesale; the
        # partial result is returned with incomplete=True.
        try:
            # Build both agents fresh for this game. If the second fails to
            # construct, close the first so a half-built pair never leaks a
            # process (subprocess bots hold an OS process open).
            agent_a = a_factory(seed_a)
            try:
                agent_b = b_factory(seed_b)
            except BaseException:
                _maybe_close(agent_a)
                raise

            p0, p1 = (agent_a, agent_b) if a_is_p0 else (agent_b, agent_a)
            try:
                res: MatchResult = play_match(game_factory(), p0, p1, max_turns=max_turns)
            finally:
                _maybe_close(agent_a)
                _maybe_close(agent_b)
        except KeyboardInterrupt:
            incomplete = True
            break
        except Exception as e:
            print(
                f"warning: game {i + 1} could not be run ({type(e).__name__}: {e}); "
                f"stopping with {completed} game(s) completed",
                file=sys.stderr,
            )
            incomplete = True
            break

        completed += 1
        total_turns += res.turns
        reason_counts[res.reason] = reason_counts.get(res.reason, 0) + 1

        # Attribute each recorded think-time to the contestant who spent it.
        for rec in res.move_history:
            is_a = (rec.player == 0) == a_is_p0
            (a_ms if is_a else b_ms).append(rec.ms)

        if res.winner is None:
            draws += 1
        else:
            a_won = (res.winner == 0) == a_is_p0
            if a_won:
                a_wins += 1
            else:
                b_wins += 1
            # The loser forfeited iff the terminal reason is a forfeit one.
            if res.reason in FORFEIT_REASONS:
                if a_won:
                    b_forfeits += 1
                else:
                    a_forfeits += 1

        if progress is not None:
            progress(BenchmarkProgress(completed, games, a_wins, b_wins, draws))

    return BenchmarkResult(
        game=game_name,
        a_label=a_label,
        b_label=b_label,
        games=completed,
        a_wins=a_wins,
        b_wins=b_wins,
        draws=draws,
        a_forfeits=a_forfeits,
        b_forfeits=b_forfeits,
        reason_counts=reason_counts,
        avg_turns=(total_turns / completed if completed else 0.0),
        a_avg_ms=(statistics.fmean(a_ms) if a_ms else 0.0),
        a_max_ms=(max(a_ms) if a_ms else 0.0),
        b_avg_ms=(statistics.fmean(b_ms) if b_ms else 0.0),
        b_max_ms=(max(b_ms) if b_ms else 0.0),
        incomplete=incomplete,
    )


def _pct(n: int, total: int) -> float:
    return (100.0 * n / total) if total else 0.0


def _disambiguated_labels(a_label: str, b_label: str) -> tuple[str, str]:
    # When both contestants share a name (e.g. greedy vs greedy), tag them so
    # the per-contestant lines stay distinguishable.
    if a_label == b_label:
        return f"{a_label} (A)", f"{b_label} (B)"
    return a_label, b_label


def format_summary(r: BenchmarkResult) -> str:
    a_disp, b_disp = _disambiguated_labels(r.a_label, r.b_label)
    status = "INTERRUPTED" if r.incomplete else "completed"

    lines = [
        f"game: {r.game}",
        f"games: {r.games} ({status})",
        f"  {a_disp}: {r.a_wins} wins ({_pct(r.a_wins, r.games):.1f}%)",
        f"  {b_disp}: {r.b_wins} wins ({_pct(r.b_wins, r.games):.1f}%)",
        f"  draws: {r.draws} ({_pct(r.draws, r.games):.1f}%)",
    ]
    if r.a_forfeits or r.b_forfeits:
        lines.append(f"forfeits: {a_disp}={r.a_forfeits}, {b_disp}={r.b_forfeits}")
    lines.append(f"avg turns: {r.avg_turns:.1f}")
    lines.append(
        f"think ms (avg/max): {a_disp} {r.a_avg_ms:.1f}/{r.a_max_ms:.1f}"
        f" | {b_disp} {r.b_avg_ms:.1f}/{r.b_max_ms:.1f}"
    )
    if r.reason_counts:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(r.reason_counts.items()))
        lines.append(f"reasons: {reasons}")
    return "\n".join(lines)


def cmd_benchmark(args: argparse.Namespace) -> int:
    game_factory = _game_factory(args.game)
    a_factory = _seeded_agent_factory(args.p0)
    b_factory = _seeded_agent_factory(args.p1)

    games = int(args.games)
    if games <= 0:
        raise ValueError("--games must be a positive integer")

    quiet = bool(args.quiet)
    # Cap live progress to ~20 lines regardless of game count, so a big run does
    # not flood stdout; always print the last update.
    step = max(1, games // 20)

    def on_progress(p: BenchmarkProgress) -> None:
        if quiet:
            return
        if p.completed == p.total or p.completed % step == 0:
            print(
                f"  [{p.completed}/{p.total}] {args.p0} {p.a_wins}-{p.draws}-{p.b_wins} {args.p1}"
                " (W-D-L)",
                flush=True,
            )

    result = run_benchmark(
        game_factory=game_factory,
        a_factory=a_factory,
        b_factory=b_factory,
        a_label=args.p0,
        b_label=args.p1,
        games=games,
        swap_starts=not args.no_swap,
        base_seed=args.seed,
        max_turns=int(args.max_turns),
        progress=on_progress,
    )

    print(format_summary(result))

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        try:
            atomic_write_json(out_path, asdict(result))
            print(f"out: {out_path}")
        except Exception as e:  # best-effort: the summary is already on stdout
            print(f"warning: failed to write results to {out_path} ({type(e).__name__}: {e})", file=sys.stderr)

    # 130 = interrupted (128 + SIGINT), matching shell convention, so callers
    # can tell a partial run from a complete one.
    return 130 if result.incomplete else 0


def load_benchmark_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("benchmark", help="Play many games between two agents and report head-to-head rates")
    p.add_argument("game", help="Built-in name (e.g. tictactoe) or '<path>:<symbol>'")
    p.add_argument("--p0", required=True, help="Contestant A: random|greedy|search|subprocess:<cmd>|<path>:<symbol>")
    p.add_argument("--p1", required=True, help="Contestant B: random|greedy|search|subprocess:<cmd>|<path>:<symbol>")
    p.add_argument("--games", type=int, default=100, help="Number of games to play (default 100)")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Base seed for reproducible, distinct-per-game play of the built-in agents",
    )
    p.add_argument(
        "--no-swap",
        action="store_true",
        help="Keep --p0 as the starting player every game (default: alternate starts)",
    )
    p.add_argument("--max-turns", type=int, default=10_000, help="Hard cap on turns per game")
    p.add_argument("--out", help="Write JSON results to this path")
    p.add_argument("--quiet", action="store_true", help="Suppress per-game progress; print only the final summary")
    p.set_defaults(func=cmd_benchmark)

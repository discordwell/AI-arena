from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .agents.random_agent import RandomAgent
from .benchmark import _maybe_close, _seeded_agent_factory
from .json_types import JSONValue
from .tournament import _game_factory

# ---------------------------------------------------------------------------
# Game conformance checker
#
# The arena's first design rule is that the harness never trusts a game: games
# are competitor-authored, dropped into model folders, and loaded dynamically,
# and the engine treats any exception / timeout / illegal move as a forfeit
# rather than a crash. That keeps a *running* tournament safe, but it gives no
# way to find out *before* an expensive run (every move can be an LLM call) that
# a freshly dropped-in game violates the protocol the engine, replay, and the
# baseline agents all rely on.
#
# `check-game` is that pre-flight check. Using only the Game protocol it runs a
# battery of game-agnostic conformance checks against a game and reports them as
# pass / warn / fail, so a broken game is caught at the desk rather than mid
# tournament. The checks are grounded in this repo's own history:
#
#   - apply_move (and the read-only methods) must NOT mutate their input state.
#     The engine, replay, and every baseline agent (greedy / search / mcts) rely
#     on "apply_move returns a new state without mutating its input"; a past bug
#     (`Photon legal_moves mutating its scratch board during generation`) was
#     exactly a read-only method mutating the state handed to it.
#   - State and moves must be JSON round-trippable. Design rule #1 is that
#     everything on the wire is JSON, so a state that uses tuples (which become
#     lists) or non-string dict keys would silently differ between live play and
#     a replay / subprocess bot.
#   - legal_moves and apply_move must agree: every move legal_moves reports must
#     apply without raising.
#   - terminal must return a well-formed verdict (a real winner is None / 0 / 1).
#   - The game must actually terminate under random play, not run forever.
#
# Like the baseline agents, the checker itself never crashes on a misbehaving
# game: every game call is wrapped, and a raised exception becomes a failed
# check rather than a traceback.
# ---------------------------------------------------------------------------

# A single check's verdict.
PASS = "pass"
WARN = "warn"  # advisory: not a protocol violation, but worth a human's eye
FAIL = "fail"  # a protocol violation; `check-game` exits non-zero

# Bound the per-state apply_move purity sweep: on a game with thousands of legal
# moves we sample this many rather than apply every one (the random playouts
# exercise the rest).
_MAX_MOVES_SAMPLED = 64

_DEFAULT_PLAYOUTS = 20
# A generous ceiling: every arena game ends well within its own turn cap
# (Skysummit/Caldera 50 plies, Photon 30 moves), so a healthy game never reaches
# this. A game that hits it on *every* playout never terminates under random play
# and is flagged.
_DEFAULT_MAX_TURNS = 1_000


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GameReport:
    label: str  # the spec/name the user asked to check
    game_name: str | None  # game.name once constructed (None if construction failed)
    checks: list[CheckResult]
    playouts: int  # random self-play games actually run
    endings: dict[str, int]  # ending kind -> count ("win:0", "draw", "max_turns", ...)

    @property
    def ok(self) -> bool:
        """True when no check failed (warnings are allowed)."""
        return all(c.status != FAIL for c in self.checks)


# ---------------------------------------------------------------------------
# JSON / mutation helpers
# ---------------------------------------------------------------------------


def _strict_json(value: Any) -> tuple[str | None, str]:
    """
    Canonical strict-JSON encoding of ``value``, or ``(None, reason)``.

    ``allow_nan=False`` rejects ``NaN`` / ``Infinity``: Python's json emits them
    by default, but they are not valid JSON, so a cross-language subprocess bot
    could not parse a state containing them. ``sort_keys`` makes the string
    canonical so it can double as a cheap "did this change?" fingerprint.
    """
    try:
        return json.dumps(value, allow_nan=False, sort_keys=True), ""
    except (TypeError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def _json_issue(value: Any, what: str) -> str:
    """Return '' if ``value`` is strict-JSON and survives a round-trip, else why."""
    s, err = _strict_json(value)
    if s is None:
        return f"{what} is not JSON-serializable ({err})"
    if json.loads(s) != value:
        return (
            f"{what} changes under a JSON round-trip (e.g. tuples become lists or "
            "dict keys become strings) -- logs and subprocess bots would see a "
            "different value than live play"
        )
    return ""


def _fingerprint(state: JSONValue) -> str | None:
    """A canonical string for ``state`` used to detect mutation, or None if it is not JSON."""
    s, _ = _strict_json(state)
    return s


def _safe_repr(value: Any) -> str:
    """``repr(value)`` that never raises -- a hostile object's ``__repr__`` could."""
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------


def _call(state: JSONValue, thunk: Callable[[], Any]) -> tuple[Any, Exception | None, bool]:
    """
    Run ``thunk()`` (which uses ``state``) and report whether it mutated ``state``.

    Returns ``(result, exception_or_None, mutated)``. Mutation is judged by
    fingerprinting ``state`` immediately before and after this one call, so each
    game method is assessed in isolation -- a pure method is never blamed for an
    earlier method's mutation. When ``state`` is not JSON its fingerprint is
    ``None`` and ``mutated`` is reported False (the non-JSON state is failed by
    the initial_state check instead).
    """
    before = _fingerprint(state)
    try:
        result, exc = thunk(), None
    except Exception as e:  # noqa: BLE001 - the checker reports, never propagates
        result, exc = None, e
    return result, exc, (before is not None and before != _fingerprint(state))


def check_game(
    game: Any,
    *,
    playouts: int = _DEFAULT_PLAYOUTS,
    max_turns: int = _DEFAULT_MAX_TURNS,
    seed: int | None = None,
) -> GameReport:
    """
    Run protocol-conformance checks against a constructed ``game`` object.

    Uses only the Game protocol and never raises on a misbehaving game: each
    check captures any exception and records a failure. ``playouts`` random
    self-play games (seeded by ``seed`` for reproducibility) exercise the game
    beyond its opening position. Returns a :class:`GameReport`; ``report.ok`` is
    True when no check failed.
    """
    checks: list[CheckResult] = []

    # --- name -------------------------------------------------------------
    # Read `name` defensively: a hostile game might expose it as a property that
    # raises (the checker must report that, never crash on it).
    try:
        raw_name = getattr(game, "name", None)
        name_repr = _safe_repr(raw_name)
    except Exception as e:  # noqa: BLE001 - the checker reports, never propagates
        raw_name, name_repr = None, f"<raised {type(e).__name__}: {e}>"
    game_name = raw_name if isinstance(raw_name, str) and raw_name else None
    label = game_name or type(game).__name__
    checks.append(
        CheckResult("name", PASS, name_repr) if game_name
        else CheckResult("name", FAIL, f"game.name must be a non-empty str, got {name_repr}")
    )

    # --- initial_state ----------------------------------------------------
    try:
        state = game.initial_state()
    except Exception as e:
        checks.append(CheckResult("initial_state", FAIL, f"initial_state() raised {type(e).__name__}: {e}"))
        # Nothing further can be checked without an initial state.
        return GameReport(label=label, game_name=game_name, checks=checks, playouts=0, endings={})

    issue = _json_issue(state, "initial_state()")
    checks.append(CheckResult("initial_state", FAIL, issue) if issue else CheckResult("initial_state", PASS))

    # --- terminal verdict --------------------------------------------------
    # Computed now so it can gate the legal_moves check below (a game that is
    # already terminal at the start legitimately has no moves); the CheckResult
    # itself is appended later, in report order.
    term, term_exc, term_mutated = _call(state, lambda: game.terminal(state))
    term_shape = "" if term_exc is not None else _terminal_shape_issue(term)
    # `is_terminal` is only trustworthy once the verdict is known well-formed
    # (a malformed verdict is failed by the `terminal` check below regardless).
    init_terminal = term_exc is None and not term_shape and bool(term.is_terminal)

    # --- legal_moves (both seats, must not mutate) ------------------------
    moves_by_seat: dict[int, list[JSONValue]] = {}
    legal = CheckResult("legal_moves", PASS)
    for seat in (0, 1):
        moves, exc, mutated = _call(state, lambda s=seat: game.legal_moves(state, s))
        if exc is not None:
            legal = CheckResult("legal_moves", FAIL, f"legal_moves(state, {seat}) raised {type(exc).__name__}: {exc}")
            break
        if not isinstance(moves, list):
            legal = CheckResult("legal_moves", FAIL, f"legal_moves(state, {seat}) must return a list, got {type(moves).__name__}")
            break
        if mutated:
            legal = CheckResult("legal_moves", FAIL, f"legal_moves(state, {seat}) mutated its input state")
            break
        moves_by_seat[seat] = moves
        bad = ""
        for mv in moves:
            bad = _json_issue(mv, f"a move from legal_moves(state, {seat})")
            if bad:
                break
        if bad:
            legal = CheckResult("legal_moves", FAIL, bad)
            break

    if legal.status == PASS and not init_terminal and not (moves_by_seat.get(0) or moves_by_seat.get(1)):
        # A non-terminal game in which neither seat can move is dead on arrival:
        # no match could ever start.
        legal = CheckResult("legal_moves", FAIL, "neither player has a legal move from the non-terminal initial state")
    checks.append(legal)

    # --- apply_move (sampled from the opening: no input mutation, JSON result) -
    apply_res = CheckResult("apply_move", PASS)
    for seat, moves in moves_by_seat.items():
        for mv in moves[:_MAX_MOVES_SAMPLED]:
            nxt, exc, mutated = _call(state, lambda se=seat, m=mv: game.apply_move(state, se, m))
            if exc is not None:
                apply_res = CheckResult("apply_move", FAIL, f"apply_move(state, {seat}, {mv!r}) raised {type(exc).__name__}: {exc}")
                break
            if mutated:
                apply_res = CheckResult("apply_move", FAIL, f"apply_move(state, {seat}, {mv!r}) mutated its input state")
                break
            nxt_issue = _json_issue(nxt, "the state returned by apply_move")
            if nxt_issue:
                apply_res = CheckResult("apply_move", FAIL, nxt_issue)
                break
        if apply_res.status == FAIL:
            break
    checks.append(apply_res)

    # --- terminal (shape + no mutation), using the verdict captured above --
    if term_exc is not None:
        checks.append(CheckResult("terminal", FAIL, f"terminal(state) raised {type(term_exc).__name__}: {term_exc}"))
    elif term_shape:
        checks.append(CheckResult("terminal", FAIL, term_shape))
    elif term_mutated:
        checks.append(CheckResult("terminal", FAIL, "terminal(state) mutated its input state"))
    else:
        checks.append(CheckResult("terminal", PASS))

    # --- render (returns str, must not mutate) ----------------------------
    rendered, exc, mutated = _call(state, lambda: game.render(state))
    if exc is not None:
        checks.append(CheckResult("render", FAIL, f"render(state) raised {type(exc).__name__}: {exc}"))
    elif not isinstance(rendered, str):
        checks.append(CheckResult("render", FAIL, f"render(state) must return a str, got {type(rendered).__name__}"))
    elif mutated:
        checks.append(CheckResult("render", FAIL, "render(state) mutated its input state"))
    else:
        checks.append(CheckResult("render", PASS))

    # --- random playouts (deep invariants) --------------------------------
    play_checks, endings, ran = _run_playouts(game, playouts=playouts, max_turns=max_turns, seed=seed)
    checks.extend(play_checks)

    return GameReport(label=label, game_name=game_name, checks=checks, playouts=ran, endings=endings)


def _terminal_shape_issue(t: Any) -> str:
    """
    Return '' if ``t`` is a well-formed Terminal verdict, else why not.

    Fully defensive: reading a hostile verdict's attributes (e.g. a property that
    raises) yields a failure string rather than propagating, so the checker never
    crashes on a malformed verdict object.
    """
    try:
        for attr in ("is_terminal", "winner", "reason"):
            if not hasattr(t, attr):
                return f"terminal(state) must return a Terminal (missing .{attr})"
        is_terminal, winner, reason = t.is_terminal, t.winner, t.reason
    except Exception as e:  # noqa: BLE001 - the checker reports, never propagates
        return f"terminal(state) returned a verdict whose attributes raised {type(e).__name__}: {e}"
    if not isinstance(is_terminal, bool):
        return f"terminal.is_terminal must be a bool, got {type(is_terminal).__name__}"
    # `True`/`False` satisfy `in (None, 0, 1)` (bool is a subclass of int), so
    # reject them explicitly -- winner is typed `PlayerId | None`, i.e. int|None.
    if winner is not None and (type(winner) is not int or winner not in (0, 1)):
        return f"terminal.winner must be None, 0, or 1, got {_safe_repr(winner)}"
    if not isinstance(reason, str):
        return f"terminal.reason must be a str, got {type(reason).__name__}"
    return ""


def _run_playouts(
    game: Any,
    *,
    playouts: int,
    max_turns: int,
    seed: int | None,
) -> tuple[list[CheckResult], dict[str, int], int]:
    """
    Play ``playouts`` random self-play games, checking deep invariants at every
    ply, and aggregate the findings into a handful of :class:`CheckResult`s plus
    an ending-kind histogram.

    The loop mirrors the engine's turn structure (check ``terminal`` before each
    move, forfeit a player with no legal move, alternate seats) so what it
    exercises matches how a real match is driven. Per ply it verifies the state
    is JSON round-trippable, that no game method mutated the state, that the
    chosen legal move applies without raising, and that any terminal verdict is
    well-formed.
    """
    n = max(0, int(playouts))
    cap = max(1, int(max_turns))
    rng = random.Random(seed)

    endings: dict[str, int] = {}
    # First concrete example of each failure class, so the report is actionable.
    raised = ""  # game code raised on a legal input
    not_json = ""  # a state was not JSON round-trippable
    mutated = ""  # a game method mutated the live state
    bad_winner = ""  # a terminal verdict was malformed
    ran = 0

    for _ in range(n):
        state = game.initial_state()
        player = 0
        outcome: str | None = None
        for _turn in range(cap):
            fp = _fingerprint(state)
            if not not_json:
                issue = _json_issue(state, "a state reached during play")
                if issue:
                    not_json = issue

            try:
                t = game.terminal(state)
            except Exception as e:
                raised = raised or f"terminal(state) raised {type(e).__name__}: {e}"
                outcome = "error"
                break
            if fp is not None and not mutated and _fingerprint(state) != fp:
                mutated = "terminal(state) mutated its input state"
            shape = _terminal_shape_issue(t)
            if shape:
                bad_winner = bad_winner or shape
                outcome = "error"
                break
            if t.is_terminal:
                outcome = "draw" if t.winner is None else f"win:{t.winner}"
                break

            try:
                legal = game.legal_moves(state, player)
            except Exception as e:
                raised = raised or f"legal_moves(state, {player}) raised {type(e).__name__}: {e}"
                outcome = "error"
                break
            if fp is not None and not mutated and _fingerprint(state) != fp:
                mutated = f"legal_moves(state, {player}) mutated its input state"
            if not isinstance(legal, list) or not legal:
                # The engine forfeits a player with no legal move; record it and
                # stop (a healthy game usually ends via `terminal`).
                outcome = "no_legal_moves"
                break

            move = rng.choice(legal)
            try:
                nxt = game.apply_move(state, player, move)
            except Exception as e:
                raised = raised or f"apply_move(state, {player}, {move!r}) raised {type(e).__name__}: {e}"
                outcome = "error"
                break
            if fp is not None and not mutated and _fingerprint(state) != fp:
                mutated = f"apply_move(state, {player}, {move!r}) mutated its input state"
            state = nxt
            player = 1 - player
        else:
            outcome = "max_turns"

        endings[outcome] = endings.get(outcome, 0) + 1
        ran += 1

    checks: list[CheckResult] = []
    if n == 0:
        return checks, endings, ran

    checks.append(
        CheckResult("playout/no-exceptions", FAIL, raised) if raised
        else CheckResult("playout/no-exceptions", PASS, f"{ran} game(s)")
    )
    checks.append(
        CheckResult("playout/json", FAIL, not_json) if not_json
        else CheckResult("playout/json", PASS)
    )
    checks.append(
        CheckResult("playout/purity", FAIL, mutated) if mutated
        else CheckResult("playout/purity", PASS)
    )
    checks.append(
        CheckResult("playout/winner", FAIL, bad_winner) if bad_winner
        else CheckResult("playout/winner", PASS)
    )

    capped = endings.get("max_turns", 0)
    if capped == ran:
        checks.append(
            CheckResult(
                "playout/terminates",
                FAIL,
                f"all {ran} random game(s) hit the {cap}-turn cap without ending; "
                "the game may never terminate under random play",
            )
        )
    elif capped:
        checks.append(
            CheckResult(
                "playout/terminates",
                WARN,
                f"{capped}/{ran} random game(s) hit the {cap}-turn cap "
                "(raise --max-turns if the game is legitimately long)",
            )
        )
    else:
        checks.append(CheckResult("playout/terminates", PASS, _endings_str(endings)))

    return checks, endings, ran


def _endings_str(endings: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(endings.items())) if endings else ""


# ---------------------------------------------------------------------------
# Formatting + CLI
# ---------------------------------------------------------------------------

_GLYPH = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}


def _header_line(kind: str, primary: str | None, label: str) -> str:
    """``"check-x: <name>  (from <label>)"``, shared by both report formatters."""
    header = f"{kind}: {primary or label}"
    if primary and primary != label:
        header += f"  (from {label})"
    return header


def _check_rows(checks: list[CheckResult]) -> list[str]:
    name_w = max((len(c.name) for c in checks), default=0)
    rows = []
    for c in checks:
        line = f"  [{_GLYPH.get(c.status, c.status)}] {c.name:<{name_w}}"
        if c.detail:
            line += f"  {c.detail}"
        rows.append(line)
    return rows


def _verdict_lines(checks: list[CheckResult], ok: bool) -> list[str]:
    fails = sum(1 for c in checks if c.status == FAIL)
    warns = sum(1 for c in checks if c.status == WARN)
    if ok:
        verdict = "PASS" if not warns else f"PASS ({warns} warning(s))"
    else:
        verdict = f"FAIL ({fails} check(s) failed)"
    return ["", f"verdict: {verdict}"]


def format_report(report: GameReport) -> str:
    lines = [_header_line("check-game", report.game_name, report.label)]
    lines.extend(_check_rows(report.checks))

    if report.playouts:
        lines.append("")
        lines.append(f"random playouts: {report.playouts}  ({_endings_str(report.endings) or 'none'})")

    lines.extend(_verdict_lines(report.checks, report.ok))
    return "\n".join(lines)


def cmd_check_game(args: argparse.Namespace) -> int:
    try:
        # _game_factory resolves and imports the spec eagerly, so the load can
        # raise here; construction can too. Both are the user's spec being wrong,
        # not the game's protocol -- a hard error, distinct from a FAIL verdict.
        game = _game_factory(args.game)()
    except Exception as e:
        print(f"error: could not load game {args.game!r} ({type(e).__name__}: {e})", file=sys.stderr)
        return 2

    report = check_game(
        game,
        playouts=int(args.playouts),
        max_turns=int(args.max_turns),
        seed=args.seed,
    )
    print(format_report(report))
    return 0 if report.ok else 1


def load_check_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "check-game",
        help="Check that a game correctly implements the Game protocol (pre-flight conformance)",
    )
    p.add_argument("game", help="Built-in name (e.g. tictactoe) or '<path>:<symbol>'")
    p.add_argument("--playouts", type=int, default=_DEFAULT_PLAYOUTS, help=f"Random self-play games to run (default {_DEFAULT_PLAYOUTS})")
    p.add_argument("--max-turns", type=int, default=_DEFAULT_MAX_TURNS, help=f"Per-playout turn cap (default {_DEFAULT_MAX_TURNS})")
    p.add_argument("--seed", type=int, default=None, help="Seed the random playouts for a reproducible check")
    p.set_defaults(func=cmd_check_game)


# ---------------------------------------------------------------------------
# Agent conformance checker
#
# `check-game` pre-flights one untrusted half of a match; `check-agent` is its
# companion for the other half. At runtime the engine forfeits an agent that
# raises, times out, or returns an illegal move -- and the tournament forfeits a
# competitor whose agent fails to start (`agent_spawn_failed:...`) -- but, as
# with games, nothing could discover any of that *before* an expensive run.
# Worse, two agent defects are invisible even at runtime: the engine hands the
# agent the LIVE state object and legal-move list (engine.play_match) and later
# validates the returned move against that same list, so an agent that mutates
# the state silently corrupts the match from that ply on, and one that adds to
# the legal list can slip an illegal move past the engine's own forfeit
# detection. The checker enforces both here, where paying for the fingerprints
# is acceptable.
#
# The checked agent plays seeded games against a seeded `random` opponent,
# alternating seats, with every one of its calls instrumented:
#
#   - construction must succeed (a subprocess bot that fails to spawn, or an
#     LLM bot with a broken API key, fails here / on its first move -- exactly
#     the tournament's `agent_spawn_failed` forfeit, caught at the desk);
#   - `name` must be a non-empty str and `select_move` callable;
#   - every returned move must be one of the supplied legal moves (checked
#     against a pre-call snapshot, exactly as the engine judges it);
#   - no exception or TimeoutError may escape `select_move`;
#   - `select_move` must not mutate the live state or the legal-move list.
#     Pure in-place reordering (e.g. an unshielded shuffle) is a warning:
#     harmless to today's engine, but it is engine-owned data.
#
# The game is presumed conforming -- run `check-game` on it first; if the game
# itself misbehaves mid-check the report says so (a warning pointing at
# check-game) instead of blaming the agent. Games default to 2 (one per seat)
# because a checked agent may be an LLM bot whose every move is a paid API
# call; a failing game stops the check early for the same reason.
# ---------------------------------------------------------------------------

_DEFAULT_AGENT_GAMES = 2


@dataclass(frozen=True, slots=True)
class AgentReport:
    label: str  # the spec/name the user asked to check
    agent_name: str | None  # agent.name once constructed (None if construction failed)
    game_name: str | None  # the game the agent was exercised on (None before one ran)
    checks: list[CheckResult]
    games: int  # instrumented games actually run
    endings: dict[str, int]  # agent-perspective ending -> count ("win:...", "forfeit:...", ...)
    moves: int  # checked-agent moves observed
    avg_ms: float  # think time over those moves
    max_ms: float

    @property
    def ok(self) -> bool:
        """True when no check failed (warnings are allowed)."""
        return all(c.status != FAIL for c in self.checks)


@dataclass(slots=True)
class _AgentGameOutcome:
    """One instrumented game's findings (first concrete example per failure class)."""

    ending: str = "max_turns"
    agent_ms: list[float] = field(default_factory=list)
    illegal: str = ""  # returned a move not in legal_moves
    raised: str = ""  # select_move raised / timed out
    impure: str = ""  # mutated the live state or the legal-move set
    reordered: str = ""  # reordered the legal-move list in place (advisory)
    game_error: str = ""  # the (presumed-conforming) game misbehaved: check aborted
    agent_failed: bool = False  # the agent itself failed; stop scheduling further games


def check_agent(
    agent_factory: Callable[[int | None], Any],
    game_factory: Callable[[], Any],
    *,
    games: int = _DEFAULT_AGENT_GAMES,
    max_turns: int = _DEFAULT_MAX_TURNS,
    seed: int | None = None,
    label: str = "agent",
) -> AgentReport:
    """
    Run conformance checks against the agent built by ``agent_factory``.

    ``agent_factory`` takes a per-game seed (or None), like benchmark's
    factories; a fresh agent is built for each game (so a stateful subprocess
    bot is spawned exactly as the tournament would) and closed afterwards. Game
    ``i`` seeds the agent with ``seed + 2i`` and the random opponent with
    ``seed + 2i + 1``, so the whole check replays identically under ``--seed``.

    Never raises on a misbehaving agent: every finding becomes a failed check in
    the returned :class:`AgentReport` (``report.ok`` is True when none failed).
    The agent plays ``games`` instrumented matches against a seeded random
    opponent, alternating seats; the first game in which the agent fails ends
    the run early (an agent that failed once will usually fail again, and every
    further game may be real money).
    """
    n_games = max(0, int(games))
    cap = max(1, int(max_turns))

    checks: list[CheckResult] = []
    endings: dict[str, int] = {}
    all_ms: list[float] = []
    ran = 0

    agent_name: str | None = None
    game_name: str | None = None
    name_check: CheckResult | None = None
    select_check: CheckResult | None = None

    # First concrete example of each failure class, so the report is actionable.
    construct_fail = ""
    illegal = ""
    raised = ""
    impure = ""
    reordered = ""
    game_error = ""

    # With --games 0 still construct once: spawn failure is the single most
    # valuable pre-flight finding and costs no agent moves to discover.
    for i in range(max(n_games, 1)):
        agent_seed = None if seed is None else seed + 2 * i
        opp_seed = None if seed is None else seed + 2 * i + 1

        try:
            agent = agent_factory(agent_seed)
        except Exception as e:  # noqa: BLE001 - the checker reports, never propagates
            suffix = f" (game {i + 1})" if i else ""
            construct_fail = f"agent construction raised {type(e).__name__}: {e}{suffix}"
            break

        try:
            if i == 0:
                agent_name, name_check, select_check = _agent_shape(agent)
                if select_check.status == FAIL:
                    break  # nothing can be played without a callable select_move
            if n_games == 0:
                break

            try:
                game = game_factory()
            except Exception as e:  # noqa: BLE001
                game_error = f"the game could not be constructed ({type(e).__name__}: {e}); run check-game on it first"
                break
            if game_name is None:
                raw = getattr(game, "name", None)
                game_name = raw if isinstance(raw, str) and raw else None

            outcome = _play_checked_game(
                game,
                agent,
                RandomAgent(seed=opp_seed),
                agent_is_p0=(i % 2 == 0),
                cap=cap,
                game_index=i + 1,
            )
            ran += 1
            endings[outcome.ending] = endings.get(outcome.ending, 0) + 1
            all_ms.extend(outcome.agent_ms)
            illegal = illegal or outcome.illegal
            raised = raised or outcome.raised
            impure = impure or outcome.impure
            reordered = reordered or outcome.reordered
            game_error = game_error or outcome.game_error
            if outcome.agent_failed or outcome.game_error:
                break
        finally:
            _maybe_close(agent)

    checks.append(
        CheckResult("construct", FAIL, construct_fail) if construct_fail
        else CheckResult("construct", PASS)
    )
    if name_check is not None:
        checks.append(name_check)
    if select_check is not None:
        checks.append(select_check)

    if ran:
        checks.append(
            CheckResult("plays/legal", FAIL, illegal) if illegal
            else CheckResult("plays/legal", PASS)
        )
        checks.append(
            CheckResult("plays/no-exceptions", FAIL, raised) if raised
            else CheckResult("plays/no-exceptions", PASS, f"{ran} game(s), {len(all_ms)} agent move(s)")
        )
        if impure:
            checks.append(CheckResult("plays/purity", FAIL, impure))
        elif reordered:
            checks.append(CheckResult("plays/purity", WARN, reordered))
        else:
            checks.append(CheckResult("plays/purity", PASS))
    if game_error:
        checks.append(CheckResult("plays/game", WARN, game_error))
    if (
        n_games
        and not all_ms
        and not (construct_fail or illegal or raised or impure)
        and select_check is not None
        and select_check.status == PASS
    ):
        # Games were requested, the agent was playable and never itself failed,
        # yet it never moved: nothing was actually validated, and PASS would be
        # a false promise. (An agent that failed its only turn is already a
        # FAIL above; this row is for the game never giving the agent a turn.)
        checks.append(
            CheckResult(
                "plays/coverage",
                FAIL,
                "no agent move was observed, so nothing was validated "
                "(did the game give the agent a turn? run check-game on the game)",
            )
        )

    return AgentReport(
        label=label,
        agent_name=agent_name,
        game_name=game_name,
        checks=checks,
        games=ran,
        endings=endings,
        moves=len(all_ms),
        avg_ms=(sum(all_ms) / len(all_ms) if all_ms else 0.0),
        max_ms=(max(all_ms) if all_ms else 0.0),
    )


def _agent_shape(agent: Any) -> tuple[str | None, CheckResult, CheckResult]:
    """The static protocol checks: ``name`` and ``select_move``, read defensively."""
    try:
        raw_name = getattr(agent, "name", None)
        name_repr = _safe_repr(raw_name)
    except Exception as e:  # noqa: BLE001 - a hostile property must not crash the checker
        raw_name, name_repr = None, f"<raised {type(e).__name__}: {e}>"
    agent_name = raw_name if isinstance(raw_name, str) and raw_name else None
    name_check = (
        CheckResult("name", PASS, name_repr) if agent_name
        else CheckResult("name", FAIL, f"agent.name must be a non-empty str, got {name_repr}")
    )

    try:
        select = getattr(agent, "select_move", None)
    except Exception as e:  # noqa: BLE001
        select, select_repr = None, f"<raised {type(e).__name__}: {e}>"
    else:
        select_repr = _safe_repr(select)
    select_check = (
        CheckResult("select_move", PASS) if callable(select)
        else CheckResult(
            "select_move",
            FAIL,
            f"agent must define a callable select_move(game, state, player, legal_moves), got {select_repr}",
        )
    )
    return agent_name, name_check, select_check


def _play_checked_game(
    game: Any,
    agent: Any,
    opponent: Any,
    *,
    agent_is_p0: bool,
    cap: int,
    game_index: int,
) -> _AgentGameOutcome:
    """
    Play one instrumented game between the checked agent and the trusted random
    opponent, mirroring ``engine.play_match``'s turn structure (terminal before
    each move, no-legal-moves forfeits the mover, strict seat alternation).

    Only the checked agent's calls are instrumented. The game is presumed
    conforming (run ``check-game`` first): a game-side exception aborts this
    game with ``game_error`` set -- reported as a warning pointing at
    check-game -- rather than blaming the agent for it.
    """
    out = _AgentGameOutcome()
    agent_seat = 0 if agent_is_p0 else 1

    def game_side(what: str, e: Exception) -> _AgentGameOutcome:
        out.game_error = (
            f"{what} raised {type(e).__name__}: {e} (game {game_index}); "
            "the game itself misbehaves -- run check-game on it"
        )
        out.ending = "check_error"
        return out

    try:
        state = game.initial_state()
    except Exception as e:  # noqa: BLE001
        return game_side("initial_state()", e)

    player = 0
    for turn in range(1, cap + 1):
        try:
            t = game.terminal(state)
            is_term, winner, reason = bool(t.is_terminal), t.winner, str(t.reason)
        except Exception as e:  # noqa: BLE001
            return game_side("terminal(state)", e)
        if is_term:
            if winner is None:
                out.ending = f"draw:{reason}"
            else:
                out.ending = f"win:{reason}" if winner == agent_seat else f"loss:{reason}"
            return out

        try:
            legal = game.legal_moves(state, player)
        except Exception as e:  # noqa: BLE001
            return game_side(f"legal_moves(state, {player})", e)
        if not isinstance(legal, list) or not legal:
            out.ending = "loss:no_legal_moves" if player == agent_seat else "win:no_legal_moves"
            return out

        if player == agent_seat:
            move = _instrumented_move(out, game, agent, state, player, legal, game_index, turn)
            if out.agent_failed:
                return out
        else:
            try:
                move = opponent.select_move(game, state, player, legal)
            except Exception as e:  # noqa: BLE001 - only reachable via a hostile game
                return game_side("the random opponent's select_move", e)

        try:
            state = game.apply_move(state, player, move)
        except Exception as e:  # noqa: BLE001
            return game_side(f"apply_move(state, {player}, {_safe_repr(move)})", e)

        player = 1 - player

    return out  # ending stays "max_turns"


def _instrumented_move(
    out: _AgentGameOutcome,
    game: Any,
    agent: Any,
    state: JSONValue,
    player: int,
    legal: list[JSONValue],
    game_index: int,
    turn: int,
) -> JSONValue:
    """
    One checked ``select_move`` call. The agent receives the *live* ``state``
    and ``legal`` objects (exactly what the engine hands it); membership is
    judged against a pre-call snapshot and mutation by pre/post fingerprints.
    On a failure, sets the finding on ``out`` and flags ``agent_failed``.
    """
    where = f"(game {game_index}, turn {turn})"
    snapshot = list(legal)
    state_fp = _fingerprint(state)
    legal_fp = _fingerprint(legal)
    elems_fp = sorted(_fingerprint(m) or "" for m in legal) if legal_fp is not None else None

    t0 = time.perf_counter()
    try:
        move = agent.select_move(game, state, player, legal)
    except TimeoutError as e:
        out.raised = (
            f"select_move timed out {where}: {e}"
            " -- a real match forfeits this agent (reason 'timeout')"
        )
        out.ending = "forfeit:timeout"
        out.agent_failed = True
        return None
    except Exception as e:  # noqa: BLE001 - the checker reports, never propagates
        out.raised = (
            f"select_move raised {type(e).__name__} {where}: {e}"
            " -- a real match forfeits this agent (reason 'agent_error')"
        )
        out.ending = "forfeit:agent_error"
        out.agent_failed = True
        return None
    out.agent_ms.append((time.perf_counter() - t0) * 1000.0)

    if state_fp is not None and _fingerprint(state) != state_fp:
        out.impure = (
            f"select_move mutated the live match state {where}"
            " -- the engine hands agents the real state object; mutating it corrupts the match"
        )
        out.ending = "aborted:state_mutation"
        out.agent_failed = True
        return None
    if legal_fp is not None and _fingerprint(legal) != legal_fp:
        after = [_fingerprint(m) for m in legal]
        if None in after or sorted(after) != elems_fp:  # type: ignore[arg-type]
            out.impure = (
                f"select_move changed the contents of the legal-move list {where}"
                " -- the engine validates the returned move against this same list, "
                "so mutating it can bypass illegal-move detection"
            )
            out.ending = "aborted:legal_moves_mutation"
            out.agent_failed = True
            return None
        out.reordered = out.reordered or (
            f"select_move reordered the legal-move list in place {where}"
            " -- harmless to today's engine, but agents should not mutate "
            "engine-owned data (copy before shuffling)"
        )

    try:
        is_legal = move in snapshot
    except Exception:  # noqa: BLE001 - a hostile __eq__ must not crash the checker
        is_legal = False
    if not is_legal:
        hint = ""
        try:
            if isinstance(move, tuple) and list(move) in snapshot:
                hint = " (hint: the move is a tuple; JSON moves are lists -- return the list form)"
        except Exception:  # noqa: BLE001
            pass
        out.illegal = (
            f"select_move returned a move not in legal_moves {where}: {_safe_repr(move)}{hint}"
            " -- a real match forfeits this agent (reason 'illegal_move')"
        )
        out.ending = "forfeit:illegal_move"
        out.agent_failed = True
        return None
    return move


def format_agent_report(report: AgentReport) -> str:
    lines = [_header_line("check-agent", report.agent_name, report.label)]
    lines.extend(_check_rows(report.checks))

    if report.games:
        lines.append("")
        game = report.game_name or "the game"
        lines.append(f"games vs seeded random on {game}: {report.games}  ({_endings_str(report.endings) or 'none'})")
        lines.append(f"agent moves: {report.moves}  (think ms avg {report.avg_ms:.1f}, max {report.max_ms:.1f})")

    lines.extend(_verdict_lines(report.checks, report.ok))
    return "\n".join(lines)


def cmd_check_agent(args: argparse.Namespace) -> int:
    if args.agent == "human":
        print("error: the 'human' agent cannot be checked (it blocks on stdin)", file=sys.stderr)
        return 2
    try:
        # Resolving the spec (a bad knob, a bad path) is the user's spec being
        # wrong -- a hard error, distinct from a FAIL verdict. Constructing the
        # agent is NOT done here: spawn failure is precisely what the checker
        # reports (the tournament's agent_spawn_failed, caught pre-flight).
        agent_factory = _seeded_agent_factory(args.agent)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not resolve agent {args.agent!r} ({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    try:
        game_factory = _game_factory(args.game)
        game_factory()  # a broken game spec is a setup error, not an agent failure
    except Exception as e:  # noqa: BLE001
        print(f"error: could not load game {args.game!r} ({type(e).__name__}: {e})", file=sys.stderr)
        return 2

    report = check_agent(
        agent_factory,
        game_factory,
        games=int(args.games),
        max_turns=int(args.max_turns),
        seed=args.seed,
        label=args.agent,
    )
    print(format_agent_report(report))
    return 0 if report.ok else 1


def load_check_agent_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "check-agent",
        help="Check that an agent constructs and plays cleanly (pre-flight conformance)",
    )
    p.add_argument(
        "agent",
        help="random|greedy|search|mcts|subprocess:<cmd>|<path>:<symbol> (built-ins accept :knob=val; 'human' cannot be checked)",
    )
    p.add_argument(
        "--game",
        default="tictactoe",
        help="Game to exercise the agent on: built-in name or '<path>:<symbol>' (default tictactoe; run check-game on it first)",
    )
    p.add_argument(
        "--games",
        type=int,
        default=_DEFAULT_AGENT_GAMES,
        help=f"Instrumented games vs a seeded random opponent, alternating seats (default {_DEFAULT_AGENT_GAMES}: an agent's every move may be a paid LLM call)",
    )
    p.add_argument("--max-turns", type=int, default=_DEFAULT_MAX_TURNS, help=f"Per-game turn cap (default {_DEFAULT_MAX_TURNS})")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Base seed for a reproducible check (seeds the built-in agents and the random opponent per game)",
    )
    p.set_defaults(func=cmd_check_agent)

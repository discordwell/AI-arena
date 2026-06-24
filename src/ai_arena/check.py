from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable

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


def format_report(report: GameReport) -> str:
    title = report.game_name or report.label
    lines = [f"check-game: {title}"]
    if report.game_name and report.game_name != report.label:
        lines[0] += f"  (from {report.label})"

    name_w = max((len(c.name) for c in report.checks), default=0)
    for c in report.checks:
        line = f"  [{_GLYPH.get(c.status, c.status)}] {c.name:<{name_w}}"
        if c.detail:
            line += f"  {c.detail}"
        lines.append(line)

    if report.playouts:
        lines.append("")
        lines.append(f"random playouts: {report.playouts}  ({_endings_str(report.endings) or 'none'})")

    fails = sum(1 for c in report.checks if c.status == FAIL)
    warns = sum(1 for c in report.checks if c.status == WARN)
    lines.append("")
    if report.ok:
        verdict = "PASS" if not warns else f"PASS ({warns} warning(s))"
    else:
        verdict = f"FAIL ({fails} check(s) failed)"
    lines.append(f"verdict: {verdict}")
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

from __future__ import annotations

import shlex
from typing import Any, Callable

from .games.tictactoe import TicTacToe
from .loading import load_symbol

# ---------------------------------------------------------------------------
# Spec resolution -- the single source of truth for turning a user-written spec
# into a game or agent factory.
#
# A *spec* is the string a user writes for a game (``ai-arena play <game>``) or
# an agent (``--p0`` / ``--p1`` / ``--agents``, and ``agent = ...`` in
# ``arena.toml``):
#
#   game:  "tictactoe"                    -- a built-in name
#          "<path>:<symbol>"              -- a class or zero-arg factory in a file
#   agent: "human"                        -- handled by each entry point (see below)
#          "random"|"greedy"|"search"|"mcts"[:knob=val,...]  -- a seedable built-in
#          "subprocess:<cmd...>"          -- a JSONL bot (docs/protocol.md)
#          "<path>:<symbol>"              -- a class or zero-arg factory in a file
#
# Every entry point (``cli`` / ``gui`` / ``benchmark`` / ``round-robin`` /
# ``tournament``) used to re-implement this dispatch inline, so the subprocess
# parsing (with its error message), the ``resolve_builtin_agent`` fall-through,
# and the ``<path>:<symbol>`` loading were each copied four times and free to
# drift. They now all route through here -- the same single-source-of-truth
# treatment ``agents/builtins.py`` already gives the tunable-agent grammar.
#
# ``human`` is deliberately NOT resolved here: its class differs by entry point
# (the CLI's stdin ``HumanAgent`` vs the GUI's click-driven one) and some entry
# points reject it outright (``benchmark`` / ``round-robin`` / ``check-agent``
# all block on stdin), so each caller handles ``human`` before delegating here.
# ---------------------------------------------------------------------------

_SUBPROCESS_PREFIX = "subprocess:"


def is_subprocess_spec(spec: str) -> bool:
    """True if ``spec`` names a subprocess (JSONL) bot."""
    return spec.startswith(_SUBPROCESS_PREFIX)


def parse_subprocess_command(spec: str) -> list[str]:
    """
    Split a ``subprocess:<cmd...>`` spec into an argv list.

    Raises ``ValueError`` on an empty command -- the one shared parser, so the
    guidance a user sees is identical no matter which command they typed into.
    """
    cmd = shlex.split(spec.removeprefix(_SUBPROCESS_PREFIX).strip())
    if not cmd:
        raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
    return cmd


def _factory_from_path(spec: str) -> Callable[[], Any]:
    """
    Resolve a ``<path>:<symbol>`` spec to a zero-arg factory.

    A class or other callable is returned as-is (called per use, so each use
    gets a fresh instance); a non-callable object is wrapped in a thunk that
    hands back that same object.
    """
    obj = load_symbol(spec)
    if callable(obj):
        return obj
    return lambda: obj


# Built-in games selectable by name. A registry (not an if-chain) so `list-games`
# and the resolver share one source of truth. Values are zero-arg factories.
BUILTIN_GAMES: dict[str, Callable[[], Any]] = {
    "tictactoe": TicTacToe,
}


def resolve_game_factory(spec: str) -> Callable[[], Any]:
    """
    Resolve a game spec to a zero-arg factory ``make() -> game``.

    A built-in name (e.g. ``"tictactoe"``) wins first; otherwise the spec is a
    ``"<path>:<symbol>"`` loaded dynamically. Callers that want an instance call
    the returned factory (``resolve_game_factory(spec)()``); callers that want to
    build many independent games (benchmark/tournament) keep the factory.
    """
    builtin = BUILTIN_GAMES.get(spec)
    if builtin is not None:
        return builtin
    return _factory_from_path(spec)


def resolve_agent_factory(spec: str) -> Callable[[int | None], Any]:
    """
    Resolve a non-``human`` agent spec to a seed-aware factory ``make(seed) -> agent``.

    Only the seedable built-ins (``random`` / ``greedy`` / ``search`` / ``mcts``)
    consume ``seed``; a subprocess bot and a ``<path>:<symbol>`` agent ignore it
    (they have no seed parameter). This lets a caller that wants reproducible
    play pass a per-game seed (``cli play --seed``, ``benchmark``) while one that
    deliberately leaves built-ins unseeded (``tournament`` / ``gui``) passes
    ``None`` -- a single resolver, two seeding policies.

    Built-in tunable parameters are validated eagerly (via
    ``resolve_builtin_agent``), so a bad knob raises here, before any match runs.
    ``human`` is not handled here (see the module docstring); resolve it first.
    """
    # Imported lazily so resolving a spec does not pull in every agent module
    # (subprocess, mcts, ...) -- matches the entry points' existing style.
    from .agents.builtins import resolve_builtin_agent

    resolved = resolve_builtin_agent(spec)
    if resolved is not None:
        cls, kwargs = resolved
        return lambda seed: cls(seed=seed, **kwargs)

    if is_subprocess_spec(spec):
        from .agents.subprocess_agent import SubprocessAgent

        cmd = parse_subprocess_command(spec)
        return lambda seed: SubprocessAgent(cmd)

    factory = _factory_from_path(spec)
    return lambda seed: factory()

#!/usr/bin/env python3
"""
JSONL subprocess bot that delegates to Claude Code (claude CLI).

Reads turn messages on stdin, calls `claude` in non-interactive mode,
and writes move responses on stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _minijson(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


# ---------------------------------------------------------------------------
# Caldera prompt
# ---------------------------------------------------------------------------

def _render_caldera_board(state: dict[str, Any]) -> str:
    board = state["board"]
    size = len(board)
    syms = {"crown": "c", "lancer": "l", "smith": "s"}
    pmap: dict[tuple[int, int], str] = {}
    for p in state.get("p0", []):
        pmap[(p["r"], p["c"])] = syms[p["type"]]
    for p in state.get("p1", []):
        pmap[(p["r"], p["c"])] = syms[p["type"]].upper()

    lines = ["  " + " ".join(str(c) for c in range(size))]
    for r in range(size):
        parts = [f"{r}"]
        for c in range(size):
            if (r, c) in pmap:
                parts.append(pmap[(r, c)])
            elif board[r][c] == -1:
                parts.append("X")
            else:
                parts.append(str(board[r][c]))
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _build_caldera_prompt(turn_msg: dict[str, Any]) -> str:
    player = turn_msg["player"]
    state = turn_msg["state"]
    legal_moves = turn_msg["legal_moves"]

    board_str = _render_caldera_board(state)
    n0 = len(state.get("p0", []))
    n1 = len(state.get("p1", []))
    ply = state.get("ply", 0)

    move_lines = []
    for m in legal_moves:
        if m["action"] == "move":
            move_lines.append(f'  {{"action":"move","from":{m["from"]},"to":{m["to"]}}}')
        else:
            move_lines.append(f'  {{"action":"forge","smith":{m["smith"]},"target":{m["target"]}}}')
    moves_str = "\n".join(move_lines)

    return (
        "You are playing CALDERA, a volcanic tactics game on a 7x7 grid.\n\n"
        "RULES SUMMARY:\n"
        "- Win by capturing/destroying the enemy Crown. Failing that, most pieces at turn limit.\n"
        "- Pieces: Crown (C, 1 step 8-dir), Lancer (L, 1-2 steps straight line, leaps), "
        "Smith (S, 1 step 8-dir, can Forge).\n"
        "- Movement: destination not Vent(-1), not friendly. Climb max +1 height/step, descend any.\n"
        "- Lancer 2-step: straight line, intermediate in-bounds & not Vent, height checked per step.\n"
        "- Forge (Smith only): raise orthogonally adjacent empty non-Vent cell by +1 height.\n"
        "- Height 4+ erupts: cell becomes Vent, destroys piece on it, raises orthogonal neighbors +1, chain.\n"
        "- Both Crowns destroyed in same eruption: active player LOSES.\n"
        "- Landing on enemy piece captures it.\n\n"
        "STRATEGY:\n"
        "- Protect your Crown at all costs. Lancers threaten from 2 cells away.\n"
        "- Use Smiths to forge terrain walls. Height 3 cells near enemies are eruption threats.\n"
        "- Control the center. Look for Lancer leaps to capture the enemy Crown.\n"
        "- Set up dual threats: Lancer attack + eruption risk.\n\n"
        f"You are Player {player} ({'lowercase' if player == 0 else 'UPPERCASE'} pieces). "
        f"Ply {ply}. P0 has {n0} pieces, P1 has {n1} pieces.\n\n"
        f"BOARD (row col, 0=top-left, X=Vent, digits=height, letters=pieces):\n{board_str}\n\n"
        f"YOUR PIECES:\n{_minijson(state.get(f'p{player}', []))}\n\n"
        f"ENEMY PIECES:\n{_minijson(state.get(f'p{1 - player}', []))}\n\n"
        f"LEGAL MOVES ({len(legal_moves)} options):\n{moves_str}\n\n"
        "THINK step by step:\n"
        "1. THREATS: What cells can each enemy piece reach next turn? (Lancers reach 2 cells in a line.)\n"
        "2. CAPTURES: Can you land on an enemy piece, especially the Crown?\n"
        "3. SAFETY: Don't move into a cell the enemy can capture next turn.\n"
        "4. CROWN SAFETY: Is your Crown in danger? If so, move it or block the threat NOW.\n"
        "5. POSITIONING: Advance toward enemy Crown, control center, set up Lancer leaps.\n\n"
        "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move must be copied exactly from the legal moves list.\n"
    )


# ---------------------------------------------------------------------------
# SkySummit prompt
# ---------------------------------------------------------------------------

def _render_skysummit_board(state: dict[str, Any]) -> str:
    board = state["board"]
    workers = state.get("workers", [[], []])
    w_map: dict[int, str] = {}
    for wi, (a, b) in enumerate(workers):
        w_map[a] = f"{'a' if wi == 0 else 'A'}"
        w_map[b] = f"{'b' if wi == 0 else 'B'}"

    size = 5
    lines = ["  " + " ".join(f"{c}" for c in range(size))]
    for r in range(size):
        parts = [f"{r}"]
        for c in range(size):
            idx = r * size + c
            h = board[idx]
            if idx in w_map:
                parts.append(f"{w_map[idx]}{h}")
            else:
                parts.append(f".{h}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _build_skysummit_prompt(turn_msg: dict[str, Any]) -> str:
    player = turn_msg["player"]
    state = turn_msg["state"]
    legal_moves = turn_msg["legal_moves"]
    phase = state.get("phase", "play")
    ply = state.get("ply", 0)

    board_str = _render_skysummit_board(state)
    moves_str = "\n".join(f"  {_minijson(m)}" for m in legal_moves[:50])
    if len(legal_moves) > 50:
        moves_str += f"\n  ... ({len(legal_moves) - 50} more)"

    return (
        "You are playing SKYSUMMIT, a move+build tower duel on a 5x5 grid.\n\n"
        "RULES:\n"
        "- WIN: Move a worker onto a height-3 space.\n"
        "- LOSE: No legal moves on your turn.\n"
        "- Each player has 2 workers. Heights: 0-3 normal, 4=dome (blocked).\n"
        "- Each turn: Move 1 worker (8-dir, max +1 climb up, any descent), "
        "then Build on adjacent space (+1 height, 3->4 creates dome).\n"
        "- Winning moves have build=null.\n\n"
        "STRATEGY:\n"
        "- Build yourself up toward height 3. If you're on height 2 adjacent to a 3, you can win.\n"
        "- Block opponents by doming (building to 4) spaces they want to reach.\n"
        "- Keep both workers active and close to the action.\n"
        "- If opponent is on height 2 near a 3, dome that 3 immediately!\n\n"
        f"You are Player {player}. Phase: {phase}. Ply: {ply}.\n"
        f"Board indices: 0-24 in row-major (0-4 top row, 5-9 second row, etc.)\n"
        f"Workers: P0={state.get('workers', [[],[]])[0]}, P1={state.get('workers', [[],[]])[1]}\n\n"
        f"BOARD (lowercase=P0 workers, UPPERCASE=P1 workers, digit=height):\n{board_str}\n\n"
        f"LEGAL MOVES ({len(legal_moves)} options):\n{moves_str}\n\n"
        "THINK step by step:\n"
        "1. Can you win this turn? (Move to a height-3 space.)\n"
        "2. Can your opponent win next turn? If so, block them (dome the target or move to block).\n"
        "3. Which move gets you closest to height 3?\n"
        "4. Where should you build? Build up your path or dome opponent's path.\n\n"
        "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move must be copied exactly from the legal moves list.\n"
    )


# ---------------------------------------------------------------------------
# Photon prompt
# ---------------------------------------------------------------------------

def _render_photon_board(state: dict[str, Any]) -> str:
    board = state["board"]
    rows = len(board)
    cols = len(board[0]) if rows else 0
    dir_arrows = ["^", ">", "v", "<"]
    mirror_syms = ["/", "\\", "/", "\\"]

    lines = ["   " + " ".join(f"{c:>2}" for c in range(cols))]
    for r in range(rows):
        parts = [f"{r:>2}"]
        for c in range(cols):
            cell = board[r][c]
            if cell is None:
                parts.append(" .")
            else:
                t = cell["type"]
                p = cell["player"]
                ori = cell.get("orientation", 0)
                tag = str(p)
                if t == "S":
                    tag = dir_arrows[ori]
                elif t == "M":
                    tag = mirror_syms[ori]
                parts.append(f"{t}{tag}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _build_photon_prompt(turn_msg: dict[str, Any]) -> str:
    player = turn_msg["player"]
    state = turn_msg["state"]
    legal_moves = turn_msg["legal_moves"]
    turn_count = state.get("turn_count", 0)

    board_str = _render_photon_board(state)
    moves_str = "\n".join(f"  {_minijson(m)}" for m in legal_moves[:60])
    if len(legal_moves) > 60:
        moves_str += f"\n  ... ({len(legal_moves) - 60} more)"

    return (
        "You are playing PHOTON, a laser tactics game on a 10x10 grid.\n\n"
        "RULES:\n"
        "- Win by destroying the enemy King with laser beams.\n"
        "- Pieces: King (K, 1HP), Shooter (S, 1HP, fires laser every turn in facing dir),\n"
        "  Mirror (M, 1HP, reflects lasers 90deg), Block (B, 2HP, absorbs lasers).\n"
        "- Each turn: Move one piece orthogonally OR Rotate one piece (Shooter/Mirror).\n"
        "- After your action, ALL Shooters (both sides) fire simultaneously.\n"
        "- Lasers: Mirror reflects, Block absorbs (-1HP), King/Shooter destroyed.\n\n"
        "STRATEGY:\n"
        "- Keep your King out of laser lines of sight!\n"
        "- Position Shooters to target enemy King through mirror bounces.\n"
        "- Use Blocks as shields. Move Mirrors to redirect lasers.\n"
        "- DANGER: After you move, YOUR Shooters also fire - don't hit your own pieces!\n"
        "- Losing Shooters = losing offensive capability. Protect them.\n\n"
        f"You are Player {player} ({'Red/top' if player == 0 else 'Blue/bottom'}). Turn {turn_count}.\n\n"
        f"BOARD (Type+Player/Dir, .=empty):\n{board_str}\n\n"
        f"LEGAL MOVES ({len(legal_moves)} options):\n{moves_str}\n\n"
        "THINK step by step:\n"
        "1. Where are enemy Shooters pointing? Trace their laser paths.\n"
        "2. Is your King in any laser path? If so, move it or block the path NOW.\n"
        "3. Can you set up a laser to hit the enemy King? (Move/rotate a Shooter or Mirror.)\n"
        "4. After your move, will any Shooter (yours or enemy) hit your own pieces?\n\n"
        "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move must be copied exactly from the legal moves list.\n"
    )


# ---------------------------------------------------------------------------
# Generic fallback prompt (for unknown games)
# ---------------------------------------------------------------------------

def _build_generic_prompt(turn_msg: dict[str, Any]) -> str:
    game = turn_msg.get("game", "unknown")
    player = turn_msg.get("player")
    state = turn_msg.get("state")
    legal_moves = turn_msg.get("legal_moves")

    return (
        "You are playing a deterministic 2-player turn-based game.\n"
        "Analyze the state carefully and choose the best strategic move.\n\n"
        f"game={game}\n"
        f"player={player}\n"
        f"state_json={_minijson(state)}\n"
        f"legal_moves_json={_minijson(legal_moves)}\n\n"
        "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move value must be exactly one element from legal_moves.\n"
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _build_prompt(turn_msg: dict[str, Any]) -> str:
    game = turn_msg.get("game", "")
    if game == "caldera":
        return _build_caldera_prompt(turn_msg)
    elif game == "skysummit":
        return _build_skysummit_prompt(turn_msg)
    elif game == "photon_laser_tactics":
        return _build_photon_prompt(turn_msg)
    else:
        return _build_generic_prompt(turn_msg)


def _query_claude(
    *,
    claude_bin: str,
    model: str,
    prompt: str,
    timeout_s: float,
) -> Any:
    cmd = [
        claude_bin,
        "--print",
        "--model", model,
        "--effort", "high",
        prompt,
    ]

    cp = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"claude failed rc={cp.returncode}: {cp.stderr.strip()[:4000]}"
        )

    raw = cp.stdout.strip()
    if not raw:
        raise ValueError("claude produced empty output")

    # Try to extract JSON from the response
    # Claude may wrap it in markdown code fences
    if "```" in raw:
        parts = raw.split("```")
        for part in parts[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                payload = json.loads(part)
                if isinstance(payload, dict) and "move" in payload:
                    return payload["move"]
            except json.JSONDecodeError:
                continue

    # Try parsing the whole thing as JSON
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and "move" in payload:
            return payload["move"]
        return payload
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start:end])
            if isinstance(payload, dict) and "move" in payload:
                return payload["move"]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"could not parse claude output: {raw[:500]}")


def _emit(resp: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code subprocess JSONL bot for ai-arena")
    parser.add_argument("--model", default=os.environ.get("OPUS_ARENA_MODEL", "claude-opus-4-6"))
    parser.add_argument("--claude-bin", default=os.environ.get("OPUS_ARENA_CLAUDE_BIN", "claude"))
    parser.add_argument("--claude-timeout-s", type=float, default=float(os.environ.get("OPUS_ARENA_TIMEOUT_S", "3500")))
    args = parser.parse_args()

    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(msg, dict) or msg.get("type") != "turn":
            continue

        legal_moves = msg.get("legal_moves")
        if not isinstance(legal_moves, list):
            legal_moves = []

        try:
            move = _query_claude(
                claude_bin=args.claude_bin,
                model=args.model,
                prompt=_build_prompt(msg),
                timeout_s=args.claude_timeout_s,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"opus_subprocess_bot_error: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": f"api_call_failed: {err}"})
            continue

        if move not in legal_moves:
            err = f"illegal move returned: {move!r}"
            print(f"opus_subprocess_bot_error: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": err})
            continue

        _emit({"type": "move", "move": move})

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

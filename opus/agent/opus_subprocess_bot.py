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


def _render_board(state: dict[str, Any]) -> str:
    """Produce a human-readable board string for the prompt."""
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


def _build_prompt(turn_msg: dict[str, Any]) -> str:
    player = turn_msg.get("player")
    state = turn_msg.get("state")
    legal_moves = turn_msg.get("legal_moves")

    board_str = _render_board(state)
    n0 = len(state.get("p0", []))
    n1 = len(state.get("p1", []))
    ply = state.get("ply", 0)

    # Group legal moves by piece for readability
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
        "- Win by capturing/destroying the enemy Crown. Failing that, most pieces at ply 200.\n"
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
        "- Use Smiths to forge terrain walls (height blocks climbing). Height 3 cells near enemies are eruption threats.\n"
        "- Control the center. High ground is defensively strong.\n"
        "- Look for Lancer leaps to capture undefended pieces, especially the enemy Crown.\n"
        "- Set up dual threats: Lancer attack + eruption risk forces the opponent into losing trades.\n\n"
        f"You are Player {player} ({'lowercase' if player == 0 else 'UPPERCASE'} pieces). "
        f"Ply {ply}. P0 has {n0} pieces, P1 has {n1} pieces.\n\n"
        f"BOARD (row col, 0=top-left, X=Vent, digits=height, letters=pieces):\n{board_str}\n\n"
        f"YOUR PIECES:\n{_minijson(state.get(f'p{player}', []))}\n\n"
        f"ENEMY PIECES:\n{_minijson(state.get(f'p{1 - player}', []))}\n\n"
        f"LEGAL MOVES ({len(legal_moves)} options):\n{moves_str}\n\n"
        "THINK CAREFULLY using these steps:\n"
        "1. THREATS: For each enemy piece, list every cell it can reach next turn "
        "(remember Lancers reach 2 cells in a line). These cells are DANGER ZONES.\n"
        "2. CAPTURES: Can you land on an enemy piece this turn? Especially the Crown.\n"
        "3. SAFETY CHECK: For each candidate move, check if the destination is in a "
        "DANGER ZONE. NEVER move a piece to a cell an enemy can capture next turn "
        "unless you are capturing something of equal or greater value.\n"
        "4. CROWN SAFETY: Is your Crown adjacent to or within Lancer-leap range of an "
        "enemy? If so, move it to safety or block the threat IMMEDIATELY.\n"
        "5. POSITIONING: Prefer moves that advance toward the enemy Crown, control the "
        "center, or set up Lancer leaps for future captures.\n\n"
        "Then output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move value must be copied exactly from the legal moves list above.\n"
    )


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
        "--max-tokens", "1024",
        prompt,
    ]

    cp = subprocess.run(
        cmd,
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
        # Extract content between code fences
        parts = raw.split("```")
        for part in parts[1::2]:  # odd indices are inside fences
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


def _fallback_legal_move(legal_moves: list[Any]) -> Any:
    if not legal_moves:
        return None
    return legal_moves[0]


def _main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code subprocess JSONL bot for ai-arena")
    parser.add_argument("--model", default=os.environ.get("OPUS_ARENA_MODEL", "claude-opus-4-6"))
    parser.add_argument("--claude-bin", default=os.environ.get("OPUS_ARENA_CLAUDE_BIN", "claude"))
    parser.add_argument("--claude-timeout-s", type=float, default=float(os.environ.get("OPUS_ARENA_TIMEOUT_S", "3500")))
    args = parser.parse_args()

    for line in sys.stdin:
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
            if move not in legal_moves:
                move = _fallback_legal_move(legal_moves)
        except Exception as e:
            print(f"opus_subprocess_bot_error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            move = _fallback_legal_move(legal_moves)

        sys.stdout.write(json.dumps({"type": "move", "move": move}) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

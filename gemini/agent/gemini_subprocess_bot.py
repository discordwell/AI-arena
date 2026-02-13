#!/usr/bin/env python3
"""
JSONL subprocess bot that delegates to Google Gemini API.

Reads turn messages on stdin, calls the Gemini API,
and writes move responses on stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from google import genai


def _minijson(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _render_board(state: dict[str, Any]) -> str:
    """Produce an ASCII board for the Gemini prompt."""
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
                # e.g. "K0", "S^", "M/", "B1"
                parts.append(f"{t}{tag}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _build_prompt(turn_msg: dict[str, Any]) -> str:
    game = turn_msg.get("game", "unknown")
    player = turn_msg.get("player")
    state = turn_msg.get("state")
    legal_moves = turn_msg.get("legal_moves")

    # Game-specific board rendering
    if game == "photon_laser_tactics":
        board_str = _render_board(state)
        turn_count = state.get("turn_count", 0)

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
            "- After your action, ALL Shooters fire simultaneously.\n"
            "- Lasers travel until hitting a piece or board edge.\n"
            "- Mirror reflects, Block absorbs (-1HP), King/Shooter/Mirror(back) destroyed.\n\n"
            "STRATEGY:\n"
            "- Protect your King from laser lines of sight.\n"
            "- Position Shooters to target enemy King through mirror bounces.\n"
            "- Use Blocks as shields. Move Mirrors to redirect lasers.\n"
            "- Watch out for YOUR OWN Shooters hitting your pieces after you move!\n\n"
            f"You are Player {player} ({'Red/top' if player == 0 else 'Blue/bottom'}). Turn {turn_count}.\n\n"
            f"BOARD (Type+Player/Direction, .=empty):\n{board_str}\n\n"
            f"LEGAL MOVES ({len(legal_moves)} options):\n{moves_str}\n\n"
            "Think step by step:\n"
            "1. Where are enemy pieces? Where are laser threat lines?\n"
            "2. Is your King in danger? If so, move it or block the threat.\n"
            "3. Can you set up a laser path to hit the enemy King?\n"
            "4. After your move, will any Shooter (yours or enemy) hit your pieces?\n\n"
            "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
            "The move must be copied exactly from the legal moves list.\n"
        )

    # Generic prompt for any game (tictactoe, caldera, skysummit, etc.)
    return (
        "You are playing a deterministic 2-player turn-based game.\n"
        "Choose exactly one legal move. Think strategically.\n\n"
        f"game={game}\n"
        f"player={player}\n"
        f"state_json={_minijson(state)}\n"
        f"legal_moves_json={_minijson(legal_moves)}\n\n"
        "Output ONLY a JSON object: {\"move\": <chosen_move>}\n"
        "The move value must be exactly one element from legal_moves.\n"
    )


def _query_gemini(
    *,
    client: genai.Client,
    model: str,
    prompt: str,
) -> Any:
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    raw = response.text.strip()

    # Strip markdown code fences
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

    # Try parsing whole response
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and "move" in payload:
            return payload["move"]
        return payload
    except json.JSONDecodeError:
        pass

    # Find JSON object in text
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start:end])
            if isinstance(payload, dict) and "move" in payload:
                return payload["move"]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"could not parse gemini output: {raw[:500]}")


def _emit(resp: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Gemini subprocess JSONL bot for ai-arena")
    parser.add_argument("--model", default=os.environ.get("GEMINI_ARENA_MODEL", "gemini-3-pro-preview"))
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY", ""))
    args = parser.parse_args()

    if not args.api_key:
        print("gemini_subprocess_bot: GOOGLE_API_KEY not set", file=sys.stderr, flush=True)
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
            _emit({"type": "error", "error": "GOOGLE_API_KEY not set"})
        return 0

    client = genai.Client(api_key=args.api_key)

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
            move = _query_gemini(
                client=client,
                model=args.model,
                prompt=_build_prompt(msg),
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"gemini_subprocess_bot_error: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": f"api_call_failed: {err}"})
            continue

        if move not in legal_moves:
            err = f"illegal move returned: {move!r}"
            print(f"gemini_subprocess_bot: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": err})
            continue

        _emit({"type": "move", "move": move})

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

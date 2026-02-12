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


def _build_prompt(turn_msg: dict[str, Any]) -> str:
    game = turn_msg.get("game")
    player = turn_msg.get("player")
    state = turn_msg.get("state")
    legal_moves = turn_msg.get("legal_moves")

    return (
        "You are playing a deterministic 2-player turn-based game.\n"
        "Choose exactly one legal move. Think carefully about strategy.\n"
        "Output ONLY a JSON object with key \"move\" whose value is one "
        "element from legal_moves, copied exactly.\n"
        "Do not output any other text, explanation, or markdown formatting.\n\n"
        f"game={game}\n"
        f"player={player}\n"
        f"state_json={_minijson(state)}\n"
        f"legal_moves_json={_minijson(legal_moves)}\n"
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

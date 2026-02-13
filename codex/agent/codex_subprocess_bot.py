#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


MOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # Use a string to carry an arbitrary JSON move value.
        "move_json": {"type": "string"},
    },
    "required": ["move_json"],
    "additionalProperties": False,
}


def _minijson(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _build_prompt(turn_msg: dict[str, Any]) -> str:
    game = turn_msg.get("game")
    player = turn_msg.get("player")
    state = turn_msg.get("state")
    legal_moves = turn_msg.get("legal_moves")

    return (
        "You are playing a deterministic 2-player turn-based game.\n"
        "Choose exactly one legal move.\n"
        "Output ONLY a JSON object with key move_json.\n"
        "move_json must be a valid JSON literal string that decodes to one element of legal_moves.\n"
        "Do not output explanations.\n\n"
        f"game={game}\n"
        f"player={player}\n"
        f"state_json={_minijson(state)}\n"
        f"legal_moves_json={_minijson(legal_moves)}\n"
    )


def _query_codex(
    *,
    codex_bin: str,
    model: str,
    reasoning_effort: str,
    prompt: str,
    workdir: Path,
    timeout_s: float,
) -> Any:
    schema_fd, schema_path = tempfile.mkstemp(prefix="arena_move_schema_", suffix=".json")
    out_fd, out_path = tempfile.mkstemp(prefix="arena_move_out_", suffix=".json")
    os.close(schema_fd)
    os.close(out_fd)

    try:
        Path(schema_path).write_text(json.dumps(MOVE_SCHEMA), encoding="utf-8")

        cmd = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--output-schema",
            schema_path,
            "-o",
            out_path,
            prompt,
        ]
        cp = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if cp.returncode != 0:
            raise RuntimeError(f"codex exec failed rc={cp.returncode}: {cp.stderr.strip()[:4000]}")

        raw = Path(out_path).read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError("codex produced empty structured output")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or "move_json" not in payload:
            raise ValueError(f"unexpected codex output payload: {payload!r}")
        move_json = payload["move_json"]
        if not isinstance(move_json, str):
            raise ValueError("move_json is not a string")
        return json.loads(move_json)
    finally:
        try:
            Path(schema_path).unlink(missing_ok=True)
        except Exception:
            pass
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass


def _emit(resp: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Codex subprocess JSONL bot for ai-arena")
    parser.add_argument("--model", default=os.environ.get("CODEX_ARENA_MODEL", "gpt-5.3-codex"))
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("CODEX_ARENA_REASONING_EFFORT", "xhigh"),
    )
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_ARENA_CODEX_BIN", "codex"))
    parser.add_argument("--workdir", default=os.environ.get("CODEX_ARENA_WORKDIR", os.getcwd()))
    parser.add_argument("--codex-timeout-s", type=float, default=float(os.environ.get("CODEX_ARENA_TIMEOUT_S", "3500")))
    args = parser.parse_args()

    workdir = Path(args.workdir).expanduser().resolve()

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
            prompt = _build_prompt(msg)
            move = _query_codex(
                codex_bin=args.codex_bin,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                prompt=prompt,
                workdir=workdir,
                timeout_s=args.codex_timeout_s,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"codex_subprocess_bot_error: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": f"api_call_failed: {err}"})
            continue

        if move not in legal_moves:
            err = f"illegal move returned: {move!r}"
            print(f"codex_subprocess_bot_error: {err}", file=sys.stderr, flush=True)
            _emit({"type": "error", "error": err})
            continue

        _emit({"type": "move", "move": move})

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


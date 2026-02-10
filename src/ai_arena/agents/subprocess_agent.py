from __future__ import annotations

import json
import selectors
import subprocess
import time
from dataclasses import dataclass
from typing import Any, TextIO

from ..game import Game, PlayerId
from ..json_types import JSONValue


@dataclass(slots=True)
class SubprocessAgent:
    """
    JSONL bot protocol (see docs/protocol.md).

    The bot is a long-running process that reads one JSON object per line from stdin and
    writes one JSON object per line to stdout.
    """

    command: list[str]
    name: str = "subprocess"
    timeout_s: float = 3600.0  # default: up to an hour per turn (matches the spec)

    def __post_init__(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._stdin: TextIO = self._proc.stdin
        self._stdout: TextIO = self._proc.stdout
        self._sel = selectors.DefaultSelector()
        self._sel.register(self._stdout, selectors.EVENT_READ)

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        finally:
            self._sel.close()

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        if self._proc.poll() is not None:
            raise RuntimeError(f"bot process exited with code {self._proc.returncode}")

        msg = {
            "type": "turn",
            "game": game.name,
            "player": player,
            "state": state,
            "legal_moves": legal_moves,
            "ts_ms": int(time.time() * 1000),
        }
        self._stdin.write(json.dumps(msg) + "\n")
        self._stdin.flush()

        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"bot timed out after {self.timeout_s}s")

            events = self._sel.select(timeout=min(0.25, remaining))
            if not events:
                continue

            line = self._stdout.readline()
            if not line:
                raise RuntimeError("bot stdout closed")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                # Allow debug logging on stdout; only JSON objects with {"type":"move"} matter.
                continue

            if not isinstance(resp, dict) or resp.get("type") != "move":
                # Ignore unknown message types to keep protocol extensible.
                continue

            if "move" not in resp:
                raise ValueError(f"bot move message missing 'move': {resp!r}")
            return resp["move"]

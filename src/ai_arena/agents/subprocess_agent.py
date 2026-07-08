from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TextIO

from ..game import Game, PlayerId
from ..json_types import JSONValue

_STDERR_TAIL_LINES = 40
_STDERR_LINE_CAP = 500


@dataclass(slots=True)
class SubprocessAgent:
    """
    JSONL bot protocol (see docs/protocol.md).

    The bot is a long-running process that reads one JSON object per line from stdin and
    writes one JSON object per line to stdout. Both pipes are drained on background
    threads: stdout so a move that arrives buffered behind another line is never
    stranded (and a chatty bot cannot block on a full pipe buffer), and stderr so its
    most recent lines can be attached to errors when the bot dies, for diagnosability.
    """

    command: list[str]
    name: str = "subprocess"
    timeout_s: float = 3600.0  # default: up to an hour per turn (matches the spec)
    _proc: subprocess.Popen[str] = field(init=False, repr=False)
    _stdin: TextIO = field(init=False, repr=False)
    _stdout: TextIO = field(init=False, repr=False)
    _stdout_lines: queue.Queue[str | None] = field(init=False, repr=False)
    _stdout_thread: threading.Thread = field(init=False, repr=False)
    _stderr_tail: deque[str] = field(init=False, repr=False)
    _stderr_lock: threading.Lock = field(init=False, repr=False)
    _stderr_thread: threading.Thread = field(init=False, repr=False)

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
        # Drain stdout on a background thread into a queue rather than mixing an
        # fd-level select() with a buffered readline(): a single read pulls every
        # currently-available line into the wrapper's userspace buffer, so a
        # subsequent select() on the fd blocks even though a complete line (e.g.
        # the move, written right after a debug line in one flush) is already
        # buffered and waiting. The thread hands select_move whole lines as they
        # arrive; None marks EOF.
        self._stdout_lines = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, name=f"{self.name}-stdout", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_tail = deque(maxlen=_STDERR_TAIL_LINES)
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name=f"{self.name}-stderr", daemon=True
        )
        self._stderr_thread.start()

    def _drain_stdout(self) -> None:
        # One queue item per stdout line, in arrival order; None marks EOF (the
        # bot's stdout closed, usually because it exited). Never raises out of
        # the thread -- a read error surfaces to select_move as EOF.
        try:
            for line in self._stdout:
                self._stdout_lines.put(line)
        except Exception:
            pass
        finally:
            self._stdout_lines.put(None)

    def _drain_stderr(self) -> None:
        stderr = self._proc.stderr
        if stderr is None:  # pragma: no cover
            return
        try:
            for line in stderr:
                line = line.rstrip("\n")
                if not line:
                    continue
                if len(line) > _STDERR_LINE_CAP:
                    line = line[:_STDERR_LINE_CAP] + "...[truncated]"
                with self._stderr_lock:
                    self._stderr_tail.append(line)
        except Exception:
            pass

    def stderr_tail(self) -> str:
        """Most recent stderr lines from the bot (bounded), for diagnostics."""
        with self._stderr_lock:
            return "\n".join(self._stderr_tail)

    def _death_context(self) -> str:
        # Give the drain thread a beat to consume the bot's last words before
        # snapshotting; once the process is gone its stderr hits EOF quickly.
        try:
            self._proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        if self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=0.5)
        tail = self.stderr_tail()
        return f"; bot stderr tail:\n{tail}" if tail else ""

    def close(self) -> None:
        # getattr-guarded: __post_init__ may have failed partway (e.g. Popen
        # raised), and __del__ still calls close() on the half-built instance.
        proc = getattr(self, "_proc", None)
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            # Terminating the process closes its pipes, so the drain threads hit
            # EOF and exit; join them briefly so they don't outlive the agent.
            for attr in ("_stdout_thread", "_stderr_thread"):
                thread = getattr(self, attr, None)
                if thread is not None and thread.is_alive():
                    thread.join(timeout=0.5)

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
            raise RuntimeError(
                f"bot process exited with code {self._proc.returncode}{self._death_context()}"
            )

        msg = {
            "type": "turn",
            "game": game.name,
            "player": player,
            "state": state,
            "legal_moves": legal_moves,
            "ts_ms": int(time.time() * 1000),
        }
        try:
            self._stdin.write(json.dumps(msg) + "\n")
            self._stdin.flush()
        except OSError as e:
            raise RuntimeError(f"failed to send turn to bot: {e}{self._death_context()}") from e

        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"bot timed out after {self.timeout_s}s")

            try:
                # Poll in short slices so the deadline is honored even if the bot
                # goes silent; the reader thread has already framed whole lines.
                line = self._stdout_lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if line is None:  # EOF: the bot's stdout closed (it likely exited)
                raise RuntimeError(f"bot stdout closed{self._death_context()}")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                # Allow debug logging on stdout; only JSON objects with {"type":"move"} matter.
                continue

            if not isinstance(resp, dict):
                continue

            if resp.get("type") == "error":
                detail = resp.get("error", "unknown error")
                raise RuntimeError(f"bot reported error: {detail}")

            if resp.get("type") != "move":
                # Ignore unknown message types to keep protocol extensible.
                continue

            if "move" not in resp:
                raise ValueError(f"bot move message missing 'move': {resp!r}")
            return resp["move"]

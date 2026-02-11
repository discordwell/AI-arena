from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .game import Game, PlayerId, Terminal
from .json_types import JSONValue
from .loading import load_symbol
from .replay import load_match_log, replay_from_log_payload


class GUIHumanAgent:
    name: str = "human"


def _builtin_games() -> dict[str, Any]:
    from .games.tictactoe import TicTacToe

    return {"tictactoe": TicTacToe}


def _load_game(spec: str) -> Game:
    builtins = _builtin_games()
    if spec in builtins:
        return builtins[spec]()  # type: ignore[return-value]
    obj = load_symbol(spec)
    return obj() if callable(obj) else obj


def _load_agent(spec: str) -> Any:
    if spec == "human":
        return GUIHumanAgent()
    if spec == "random":
        from .agents.random_agent import RandomAgent

        return RandomAgent()
    if spec.startswith("subprocess:"):
        from .agents.subprocess_agent import SubprocessAgent
        import shlex

        cmd = shlex.split(spec.removeprefix("subprocess:").strip())
        if not cmd:
            raise ValueError("subprocess agent requires a command, e.g. subprocess:python3 -u bot.py")
        return SubprocessAgent(cmd)

    obj = load_symbol(spec)
    return obj() if callable(obj) else obj


def _maybe_close(agent: Any) -> None:
    close = getattr(agent, "close", None)
    if callable(close):
        close()


def _repo_root() -> Path:
    # .../src/ai_arena/gui.py -> repo root is 2 parents up.
    return Path(__file__).resolve().parents[2]


def _infer_game_spec_from_log(payload: dict[str, Any]) -> str | None:
    name = payload.get("game") or payload.get("result", {}).get("game")
    if name == "tictactoe":
        return "tictactoe"
    if name == "skysummit":
        root = _repo_root()
        return str(root / "codex" / "game" / "game.py") + ":CodexGame"
    if name == "opus_game":
        root = _repo_root()
        return str(root / "opus" / "game" / "game.py") + ":OpusGame"
    if name == "gemini_game":
        root = _repo_root()
        return str(root / "gemini" / "game" / "game.py") + ":GeminiGame"
    return None


@dataclass(slots=True)
class MoveRow:
    turn: int
    player: PlayerId
    move: JSONValue
    ms: float | None = None
    note: str | None = None


def _is_human(agent: Any) -> bool:
    return isinstance(agent, GUIHumanAgent)


def launch_gui(args: argparse.Namespace) -> int:
    """
    Tkinter GUI for:
    - playing human vs AI (or human vs human)
    - watching AI vs AI matches turn-by-turn
    - replaying a JSON match log (engine-compatible)
    """

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Tkinter is required for ai-arena gui") from e

    class SkysummitBoard(ttk.Frame):
        def __init__(self, master: Any, *, on_click: Callable[[int], None]) -> None:
            super().__init__(master)
            self._on_click = on_click
            self._buttons: list[tk.Button] = []

            for r in range(5):
                self.rowconfigure(r, weight=1)
                for c in range(5):
                    self.columnconfigure(c, weight=1)
                    idx = r * 5 + c
                    b = tk.Button(self, text="..", width=4, height=2, command=lambda i=idx: self._on_click(i))
                    b.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                    self._buttons.append(b)

        def update_view(
            self,
            state: JSONValue,
            *,
            highlights: set[int] | None = None,
            selected: set[int] | None = None,
        ) -> None:
            s = state if isinstance(state, dict) else {}
            board = list(s.get("board", []))
            workers = s.get("workers", [[None, None], [None, None]])

            occ: dict[int, str] = {}
            try:
                w0 = workers[0]
                w1 = workers[1]
                if isinstance(w0[0], int):
                    occ[int(w0[0])] = "A"
                if isinstance(w0[1], int):
                    occ[int(w0[1])] = "B"
                if isinstance(w1[0], int):
                    occ[int(w1[0])] = "a"
                if isinstance(w1[1], int):
                    occ[int(w1[1])] = "b"
            except Exception:
                pass

            def base_color(h: int) -> tuple[str, str]:
                if h >= 4:
                    return ("#555555", "white")
                if h == 3:
                    return ("#f7e28b", "black")
                if h == 2:
                    return ("#b7e4c7", "black")
                if h == 1:
                    return ("#a9d6e5", "black")
                return ("#e9ecef", "black")

            hi = highlights or set()
            sel = selected or set()

            for i, btn in enumerate(self._buttons):
                h = int(board[i]) if i < len(board) and isinstance(board[i], int) else 0
                hch = "D" if h >= 4 else str(h)
                mark = occ.get(i, ".")
                bg, fg = base_color(h)
                if i in hi:
                    bg = "#ffd6a5"
                if i in sel:
                    bg = "#ffadad"
                btn.configure(text=f"{hch}{mark}", bg=bg, fg=fg, activebackground=bg)

    class TicTacToeBoard(ttk.Frame):
        def __init__(self, master: Any, *, on_click: Callable[[int], None]) -> None:
            super().__init__(master)
            self._on_click = on_click
            self._buttons: list[tk.Button] = []
            for r in range(3):
                self.rowconfigure(r, weight=1)
                for c in range(3):
                    self.columnconfigure(c, weight=1)
                    idx = r * 3 + c
                    b = tk.Button(self, text=".", width=4, height=2, command=lambda i=idx: self._on_click(i))
                    b.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                    self._buttons.append(b)

        def update_view(
            self,
            state: JSONValue,
            *,
            highlights: set[int] | None = None,
            selected: set[int] | None = None,
        ) -> None:
            _ = selected
            s = state if isinstance(state, dict) else {}
            board = list(s.get("board", [0] * 9))
            hi = highlights or set()
            for i, btn in enumerate(self._buttons):
                v = int(board[i]) if i < len(board) and isinstance(board[i], int) else 0
                ch = "." if v == 0 else ("X" if v == 1 else "O")
                bg = "#ffffff"
                if i in hi:
                    bg = "#ffd6a5"
                btn.configure(text=ch, bg=bg, activebackground=bg)

    class TextBoard(ttk.Frame):
        def __init__(self, master: Any) -> None:
            super().__init__(master)
            self._text = tk.Text(self, height=20, width=40, wrap="none")
            self._text.configure(state="disabled")
            self._text.pack(fill="both", expand=True)

        def update_view(self, text: str) -> None:
            self._text.configure(state="normal")
            self._text.delete("1.0", "end")
            self._text.insert("1.0", text)
            self._text.configure(state="disabled")

    class ArenaApp(tk.Tk):
        def __init__(self) -> None:
            super().__init__()

            self.title("AI Arena GUI")
            self.geometry("1100x720")

            self._explicit_game_spec = args.game
            self._live_game_spec = args.game or "tictactoe"
            self._p0_spec = args.p0
            self._p1_spec = args.p1
            self._max_turns = int(args.max_turns)
            self._auto_delay_ms = int(args.auto_delay_ms)
            self._load_log_path = Path(args.load_log).expanduser().resolve() if args.load_log else None
            self._save_log_path = Path(args.save_log).expanduser().resolve() if args.save_log else None

            self._game: Game | None = None
            self._agents: list[Any] = [None, None]

            self._states: list[JSONValue] = []
            self._moves: list[MoveRow] = []
            self._cursor: int = 0

            self._replay_mode: bool = False
            self._result: Terminal | None = None
            self._result_override: Terminal | None = None  # e.g. no_legal_moves/illegal/timeout

            self._busy: bool = False
            self._autoplay: bool = False

            # Skysummit human input state
            self._ss_place_sel: list[int] = []
            self._ss_worker_sel: int | None = None
            self._ss_dest_sel: int | None = None
            self._ss_move_map: dict[tuple[int, int, int | None], JSONValue] = {}
            self._ss_dests_by_worker: dict[int, set[int]] = {}
            self._ss_builds_by_worker_dest: dict[tuple[int, int], set[int | None]] = {}

            # UI
            self._status_var = tk.StringVar(value="")

            root = ttk.Frame(self)
            root.pack(fill="both", expand=True)

            paned = ttk.Panedwindow(root, orient="horizontal")
            paned.pack(fill="both", expand=True, padx=10, pady=10)

            self._left = ttk.Frame(paned)
            self._right = ttk.Frame(paned)
            paned.add(self._left, weight=3)
            paned.add(self._right, weight=2)

            self._board_container = ttk.Frame(self._left)
            self._board_container.pack(fill="both", expand=True)

            self._board_kind: str = "text"
            self._board_widget: Any = None

            status = ttk.Label(self._right, textvariable=self._status_var, wraplength=360, justify="left")
            status.pack(fill="x", pady=(0, 10))

            controls = ttk.Frame(self._right)
            controls.pack(fill="x", pady=(0, 10))

            self._btn_prev = ttk.Button(controls, text="Prev", command=self._on_prev)
            self._btn_next = ttk.Button(controls, text="Next", command=self._on_next)
            self._btn_live = ttk.Button(controls, text="Go Live", command=self._go_live)
            self._btn_prev.grid(row=0, column=0, sticky="ew")
            self._btn_next.grid(row=0, column=1, sticky="ew", padx=(6, 0))
            self._btn_live.grid(row=0, column=2, sticky="ew", padx=(6, 0))
            for c in range(3):
                controls.columnconfigure(c, weight=1)

            runrow = ttk.Frame(self._right)
            runrow.pack(fill="x", pady=(0, 10))
            self._auto_var = tk.BooleanVar(value=False)
            self._auto_chk = ttk.Checkbutton(runrow, text="Autoplay", variable=self._auto_var, command=self._toggle_autoplay)
            self._auto_chk.pack(side="left")

            ttk.Label(runrow, text="Delay (ms):").pack(side="left", padx=(10, 0))
            self._delay_var = tk.StringVar(value=str(self._auto_delay_ms))
            self._delay_entry = ttk.Entry(runrow, textvariable=self._delay_var, width=7)
            self._delay_entry.pack(side="left", padx=(6, 0))

            file_row = ttk.Frame(self._right)
            file_row.pack(fill="x", pady=(0, 10))
            ttk.Button(file_row, text="Load Log...", command=self._load_log_dialog).pack(side="left")
            ttk.Button(file_row, text="Save Log...", command=self._save_log_dialog).pack(side="left", padx=(8, 0))
            ttk.Button(file_row, text="Restart Match", command=self._restart_match).pack(side="left", padx=(8, 0))

            ttk.Label(self._right, text="Move History:").pack(anchor="w")
            self._hist = tk.Listbox(self._right, height=12)
            self._hist.pack(fill="both", expand=False, pady=(4, 10))
            self._hist.bind("<<ListboxSelect>>", self._on_hist_select)

            ttk.Label(self._right, text="Legal Moves (for current player):").pack(anchor="w")
            self._legal = tk.Listbox(self._right, height=10)
            self._legal.pack(fill="both", expand=True, pady=(4, 6))
            self._legal.bind("<Double-Button-1>", lambda _e: self._submit_selected_legal_move())

            self._btn_submit = ttk.Button(self._right, text="Submit Selected Move", command=self._submit_selected_legal_move)
            self._btn_submit.pack(fill="x")

            self.protocol("WM_DELETE_WINDOW", self._on_close)

            if self._load_log_path:
                self._load_log(self._load_log_path)
            else:
                self._start_match(self._live_game_spec, self._p0_spec, self._p1_spec)

        # --- lifecycle ---

        def _on_close(self) -> None:
            self._autoplay = False
            for a in self._agents:
                try:
                    _maybe_close(a)
                except Exception:
                    pass
            self.destroy()

        def _start_match(self, game_spec: str, p0_spec: str, p1_spec: str) -> None:
            self._replay_mode = False
            self._result = None
            self._result_override = None

            # Close any prior agents.
            for a in self._agents:
                try:
                    _maybe_close(a)
                except Exception:
                    pass
            self._agents = [None, None]

            self._game = _load_game(game_spec)
            self._agents[0] = _load_agent(p0_spec)
            self._agents[1] = _load_agent(p1_spec)

            self._states = [self._game.initial_state()]
            self._moves = []
            self._cursor = 0

            self._reset_skysummit_ui_state()
            self._ensure_board_widget()
            self._refresh()

        def _restart_match(self) -> None:
            if not self._live_game_spec:
                return
            self._autoplay = False
            self._auto_var.set(False)
            self._start_match(self._live_game_spec, self._p0_spec, self._p1_spec)

        def _load_log_dialog(self) -> None:
            p = filedialog.askopenfilename(title="Open match log", filetypes=[("JSON", "*.json"), ("All files", "*")])
            if not p:
                return
            try:
                self._load_log(Path(p))
            except Exception as e:
                messagebox.showerror("Load log failed", str(e))

        def _load_log(self, path: Path) -> None:
            payload = load_match_log(path)
            spec = self._explicit_game_spec if self._explicit_game_spec else None
            if not spec:
                spec = _infer_game_spec_from_log(payload)
            if not spec:
                raise ValueError("Could not infer game for this log. Relaunch with --game <spec>.")

            game = _load_game(spec)
            rep = replay_from_log_payload(game, payload)

            # Close any running agents.
            for a in self._agents:
                try:
                    _maybe_close(a)
                except Exception:
                    pass
            self._agents = [None, None]

            self._game = game
            self._replay_mode = True
            self._states = rep.states
            self._moves = [MoveRow(turn=m.turn, player=m.player, move=m.move, ms=m.ms, note=m.note) for m in rep.moves]
            self._cursor = 0
            self._result = rep.terminal if rep.terminal.is_terminal else None
            self._result_override = None

            self._reset_skysummit_ui_state()
            self._ensure_board_widget()
            self._refresh()

        # --- board + selection ---

        def _ensure_board_widget(self) -> None:
            for child in list(self._board_container.children.values()):
                child.destroy()

            game = self._game
            if game is None:
                return

            if game.name == "skysummit":
                self._board_kind = "skysummit"
                self._board_widget = SkysummitBoard(self._board_container, on_click=self._on_cell_click)
                self._board_widget.pack(fill="both", expand=True)
                return
            if game.name == "tictactoe":
                self._board_kind = "tictactoe"
                self._board_widget = TicTacToeBoard(self._board_container, on_click=self._on_cell_click)
                self._board_widget.pack(fill="both", expand=True)
                return

            self._board_kind = "text"
            self._board_widget = TextBoard(self._board_container)
            self._board_widget.pack(fill="both", expand=True)

        def _reset_skysummit_ui_state(self) -> None:
            self._ss_place_sel = []
            self._ss_worker_sel = None
            self._ss_dest_sel = None
            self._ss_move_map = {}
            self._ss_dests_by_worker = {}
            self._ss_builds_by_worker_dest = {}

        def _on_cell_click(self, idx: int) -> None:
            if self._busy or self._replay_mode:
                return
            if not self._is_at_latest():
                return
            if self._game is None:
                return

            player = self._player_to_act()
            agent = self._agents[player]
            if not _is_human(agent):
                return

            state = self._states[-1]
            legal = self._current_legal_moves()
            if legal is None:
                return

            if self._game.name == "tictactoe":
                if idx in legal:
                    self._apply_live_move(idx, ms=None, note=None)
                return

            if self._game.name != "skysummit":
                return

            s = state if isinstance(state, dict) else {}
            phase = s.get("phase")

            if phase == "place":
                if idx in self._ss_place_sel:
                    self._ss_place_sel.remove(idx)
                else:
                    if len(self._ss_place_sel) < 2:
                        self._ss_place_sel.append(idx)

                if len(self._ss_place_sel) == 2:
                    move = {"t": "place", "to": [self._ss_place_sel[0], self._ss_place_sel[1]]}
                    if move in legal:
                        self._ss_place_sel = []
                        self._apply_live_move(move, ms=None, note=None)
                    else:
                        self._ss_place_sel = []
                        self._refresh()
                else:
                    self._refresh()
                return

            if phase != "play":
                return

            workers = s.get("workers")
            wpos = None
            try:
                wpos = workers[player]  # type: ignore[index]
            except Exception:
                wpos = None

            if self._ss_worker_sel is None:
                if isinstance(wpos, list) and len(wpos) == 2:
                    if wpos[0] == idx:
                        self._ss_worker_sel = 0
                    elif wpos[1] == idx:
                        self._ss_worker_sel = 1
                self._refresh()
                return

            if self._ss_dest_sel is None:
                w = self._ss_worker_sel
                if idx in self._ss_dests_by_worker.get(w, set()):
                    self._ss_dest_sel = idx
                    builds = self._ss_builds_by_worker_dest.get((w, idx), set())
                    if None in builds:
                        mv = self._ss_move_map.get((w, idx, None))
                        if mv is not None:
                            self._reset_skysummit_ui_state()
                            self._apply_live_move(mv, ms=None, note=None)
                            return
                self._refresh()
                return

            w = self._ss_worker_sel
            to = self._ss_dest_sel
            builds = self._ss_builds_by_worker_dest.get((w, to), set())
            if idx in builds:
                mv = self._ss_move_map.get((w, to, idx))
                if mv is not None:
                    self._reset_skysummit_ui_state()
                    self._apply_live_move(mv, ms=None, note=None)
                    return

            self._ss_dest_sel = None
            self._refresh()

        def _submit_selected_legal_move(self) -> None:
            if self._busy or self._replay_mode:
                return
            if not self._is_at_latest():
                return
            if self._game is None:
                return

            player = self._player_to_act()
            agent = self._agents[player]
            if not _is_human(agent):
                return

            legal = self._current_legal_moves()
            if legal is None:
                return

            sel = self._legal.curselection()
            if not sel:
                return
            idx = int(sel[0])
            if not (0 <= idx < len(legal)):
                return
            move = legal[idx]
            self._reset_skysummit_ui_state()
            self._apply_live_move(move, ms=None, note=None)

        # --- navigation + autoplay ---

        def _toggle_autoplay(self) -> None:
            self._autoplay = bool(self._auto_var.get())
            try:
                self._auto_delay_ms = int(self._delay_var.get().strip())
            except ValueError:
                self._auto_delay_ms = 250
                self._delay_var.set(str(self._auto_delay_ms))
            if self._autoplay:
                self.after(self._auto_delay_ms, self._autoplay_tick)

        def _autoplay_tick(self) -> None:
            if not self._autoplay:
                return
            if self._busy:
                self.after(self._auto_delay_ms, self._autoplay_tick)
                return

            if self._cursor < len(self._states) - 1:
                self._cursor += 1
                self._refresh()
                self.after(self._auto_delay_ms, self._autoplay_tick)
                return

            if self._replay_mode:
                self._autoplay = False
                self._auto_var.set(False)
                return

            if self._is_match_over():
                self._autoplay = False
                self._auto_var.set(False)
                return

            player = self._player_to_act()
            if _is_human(self._agents[player]):
                self._autoplay = False
                self._auto_var.set(False)
                return

            self._step_live()

        def _on_prev(self) -> None:
            if self._cursor > 0:
                self._cursor -= 1
                self._refresh()

        def _on_next(self) -> None:
            if self._cursor < len(self._states) - 1:
                self._cursor += 1
                self._refresh()
                return
            if self._replay_mode:
                return
            self._step_live()

        def _on_hist_select(self, _e: Any) -> None:
            sel = self._hist.curselection()
            if not sel:
                return
            idx = int(sel[0])
            self._cursor = min(max(idx + 1, 0), len(self._states) - 1)
            self._refresh()

        def _go_live(self) -> None:
            self._cursor = len(self._states) - 1
            self._refresh()

        # --- live stepping ---

        def _player_to_act(self) -> PlayerId:
            return int(len(self._moves) % 2)

        def _is_at_latest(self) -> bool:
            return self._cursor == len(self._states) - 1

        def _is_match_over(self) -> bool:
            if self._result_override is not None:
                return self._result_override.is_terminal
            if self._result is not None:
                return self._result.is_terminal
            if self._game is None:
                return True
            t = self._game.terminal(self._states[-1])
            return t.is_terminal

        def _current_terminal(self) -> Terminal:
            if self._result_override is not None:
                return self._result_override
            if self._result is not None:
                return self._result
            if self._game is None:
                return Terminal(is_terminal=True, winner=None, reason="no_game")
            return self._game.terminal(self._states[-1])

        def _current_legal_moves(self) -> list[JSONValue] | None:
            if self._game is None:
                return None
            if self._is_match_over():
                return None
            state = self._states[-1]
            player = self._player_to_act()
            legal = self._game.legal_moves(state, player)
            if not legal:
                self._result_override = Terminal(is_terminal=True, winner=1 - player, reason="no_legal_moves")
                if self._save_log_path:
                    try:
                        self._write_log(self._save_log_path)
                    except Exception:
                        pass
                return None
            return legal

        def _step_live(self) -> None:
            if self._busy or self._game is None:
                return
            if self._is_match_over():
                return
            if len(self._moves) >= self._max_turns:
                self._result_override = Terminal(is_terminal=True, winner=None, reason="max_turns")
                if self._save_log_path:
                    try:
                        self._write_log(self._save_log_path)
                    except Exception:
                        pass
                self._refresh()
                return

            player = self._player_to_act()
            agent = self._agents[player]
            if _is_human(agent):
                self._refresh()
                return

            legal = self._current_legal_moves()
            if legal is None:
                self._refresh()
                return

            state = self._states[-1]
            game = self._game

            self._busy = True
            self._btn_next.configure(state="disabled")
            self._status_var.set(self._status_var.get() + "\n(thinking...)")

            def worker() -> None:
                t0 = time.perf_counter()
                try:
                    mv = agent.select_move(game, state, player, legal)
                    ms = (time.perf_counter() - t0) * 1000.0
                    err: Exception | None = None
                except Exception as e:  # includes TimeoutError
                    mv = None
                    ms = (time.perf_counter() - t0) * 1000.0
                    err = e

                def done() -> None:
                    self._busy = False
                    self._btn_next.configure(state="normal")
                    if err is not None:
                        reason = "timeout" if isinstance(err, TimeoutError) else "agent_error"
                        self._end_live_with_forfeit(player, reason, f"{type(err).__name__}:{err}", ms)
                        return
                    assert mv is not None
                    if mv not in legal:
                        self._end_live_with_forfeit(player, "illegal_move", "illegal_move", ms, move=mv)
                        return
                    self._apply_live_move(mv, ms=ms, note=None)

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _apply_live_move(self, move: JSONValue, *, ms: float | None, note: str | None) -> None:
            assert self._game is not None
            player = self._player_to_act()
            state = self._states[-1]

            s2 = self._game.apply_move(state, player, move)
            self._states.append(s2)
            self._moves.append(MoveRow(turn=len(self._moves) + 1, player=player, move=move, ms=ms, note=note))

            if self._cursor == len(self._states) - 2:
                self._cursor = len(self._states) - 1

            t = self._game.terminal(s2)
            if t.is_terminal:
                self._result = t

            self._refresh()

            if self._result is not None and self._result.is_terminal and self._save_log_path:
                try:
                    self._write_log(self._save_log_path)
                except Exception:
                    pass

            if self._autoplay:
                self.after(self._auto_delay_ms, self._autoplay_tick)

        def _end_live_with_forfeit(
            self,
            player: PlayerId,
            reason: str,
            note: str,
            ms: float | None,
            *,
            move: JSONValue | None = None,
        ) -> None:
            self._moves.append(MoveRow(turn=len(self._moves) + 1, player=player, move=move, ms=ms, note=note))
            self._result_override = Terminal(is_terminal=True, winner=1 - player, reason=reason)
            self._refresh()

            if self._save_log_path:
                try:
                    self._write_log(self._save_log_path)
                except Exception:
                    pass

        # --- log IO ---

        def _save_log_dialog(self) -> None:
            if self._game is None:
                return
            p = filedialog.asksaveasfilename(
                title="Save match log",
                defaultextension=".json",
                filetypes=[("JSON", "*.json"), ("All files", "*")],
            )
            if not p:
                return
            try:
                self._write_log(Path(p))
            except Exception as e:
                messagebox.showerror("Save log failed", str(e))

        def _write_log(self, path: Path) -> None:
            assert self._game is not None
            final_state = self._states[-1]
            terminal = self._current_terminal()

            payload = {
                "game": self._game.name,
                "result": {
                    "game": self._game.name,
                    "winner": terminal.winner,
                    "reason": terminal.reason,
                    "turns": len(self._moves),
                    "move_history": [
                        {
                            "turn": r.turn,
                            "player": r.player,
                            "move": r.move,
                            "ms": r.ms,
                            "note": r.note,
                        }
                        for r in self._moves
                    ],
                },
                "final_state": final_state,
                "final_render": self._game.render(final_state),
            }
            path = Path(path).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        # --- rendering / refresh ---

        def _refresh(self) -> None:
            game = self._game
            if game is None:
                self._status_var.set("No game loaded.")
                return

            cur_state = self._states[self._cursor]

            highlights: set[int] = set()
            selected: set[int] = set()

            if not self._replay_mode and self._is_at_latest() and not self._is_match_over():
                player = self._player_to_act()
                if _is_human(self._agents[player]):
                    legal = self._current_legal_moves()
                    if legal is not None:
                        if game.name == "tictactoe":
                            highlights = set(int(x) for x in legal if isinstance(x, int))
                        elif game.name == "skysummit":
                            self._index_skysummit_moves(legal)
                            s = cur_state if isinstance(cur_state, dict) else {}
                            phase = s.get("phase")
                            if phase == "place":
                                selected = set(self._ss_place_sel)
                            elif phase == "play":
                                if self._ss_worker_sel is None:
                                    try:
                                        wpos = s.get("workers", [[None, None], [None, None]])[player]
                                        highlights = {int(x) for x in wpos if isinstance(x, int)}
                                    except Exception:
                                        highlights = set()
                                elif self._ss_dest_sel is None:
                                    highlights = set(self._ss_dests_by_worker.get(self._ss_worker_sel, set()))
                                else:
                                    highlights = set(self._ss_builds_by_worker_dest.get((self._ss_worker_sel, self._ss_dest_sel), set()))  # type: ignore[arg-type]
                                    selected = {int(self._ss_dest_sel)}

            if self._board_kind == "skysummit":
                self._board_widget.update_view(cur_state, highlights=highlights, selected=selected)
            elif self._board_kind == "tictactoe":
                self._board_widget.update_view(cur_state, highlights=highlights, selected=selected)
            else:
                self._board_widget.update_view(game.render(cur_state))

            live_player = self._player_to_act() if not self._replay_mode else None
            term = self._current_terminal() if not self._replay_mode else (self._result or Terminal(False, None, ""))

            lines: list[str] = []
            lines.append(f"game: {game.name} ({'replay' if self._replay_mode else 'live'})")
            lines.append(f"frame: {self._cursor}/{len(self._states) - 1}")
            if not self._replay_mode:
                lines.append(f"to_act: player {live_player}")
                lines.append(
                    f"p0: {getattr(self._agents[0], 'name', str(self._agents[0]))} | "
                    f"p1: {getattr(self._agents[1], 'name', str(self._agents[1]))}"
                )
            if term.is_terminal:
                lines.append(f"terminal: winner={term.winner} reason={term.reason}")
            self._status_var.set("\n".join(lines))

            self._hist.delete(0, "end")
            for r in self._moves:
                suffix = f" ({r.note})" if r.note else ""
                self._hist.insert("end", f"{r.turn:03d} p{r.player}: {r.move}{suffix}")
            if self._moves:
                try:
                    sel = max(0, min(self._cursor - 1, len(self._moves) - 1))
                    self._hist.selection_clear(0, "end")
                    self._hist.selection_set(sel)
                except Exception:
                    pass

            self._legal.delete(0, "end")
            self._btn_submit.configure(state="disabled")
            if not self._replay_mode and self._is_at_latest() and not self._is_match_over():
                player = self._player_to_act()
                if _is_human(self._agents[player]):
                    legal = self._current_legal_moves()
                    if legal is not None:
                        for m in legal:
                            self._legal.insert("end", str(m))
                        self._btn_submit.configure(state="normal")

        def _index_skysummit_moves(self, legal: list[JSONValue]) -> None:
            self._ss_move_map = {}
            self._ss_dests_by_worker = {0: set(), 1: set()}
            self._ss_builds_by_worker_dest = {}

            for m in legal:
                if not isinstance(m, dict) or m.get("t") != "move":
                    continue
                w = m.get("w")
                to = m.get("to")
                build = m.get("build")
                if not (isinstance(w, int) and w in (0, 1) and isinstance(to, int)):
                    continue
                if build is not None and not isinstance(build, int):
                    continue

                key = (int(w), int(to), (None if build is None else int(build)))
                self._ss_move_map[key] = m
                self._ss_dests_by_worker[int(w)].add(int(to))
                bd_key = (int(w), int(to))
                if bd_key not in self._ss_builds_by_worker_dest:
                    self._ss_builds_by_worker_dest[bd_key] = set()
                self._ss_builds_by_worker_dest[bd_key].add(None if build is None else int(build))

    app = ArenaApp()
    app.mainloop()
    return 0

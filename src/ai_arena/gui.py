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
        """
        Canvas board with mouse-based interactions:
        - click cells
        - drag a worker and drop on destination cell
        """

        def __init__(
            self,
            master: Any,
            *,
            on_cell_click: Callable[[int], None],
            on_drag_drop: Callable[[int, int], None],
        ) -> None:
            super().__init__(master)
            self._on_cell_click = on_cell_click
            self._on_drag_drop = on_drag_drop

            self._canvas = tk.Canvas(self, bg="#102236", highlightthickness=0)
            self._canvas.pack(fill="both", expand=True)

            self._last_state: JSONValue | None = None
            self._move_hints: set[int] = set()
            self._build_hints: set[int] = set()
            self._selected: set[int] = set()
            self._piece_at: dict[int, tuple[int, int]] = {}
            self._cell_boxes: dict[int, tuple[float, float, float, float]] = {}

            self._drag_start_cell: int | None = None
            self._drag_start_xy: tuple[float, float] | None = None
            self._dragging: bool = False
            self._drag_cursor_xy: tuple[float, float] | None = None

            self._canvas.bind("<Configure>", lambda _e: self._redraw())
            self._canvas.bind("<ButtonPress-1>", self._on_press)
            self._canvas.bind("<B1-Motion>", self._on_motion)
            self._canvas.bind("<ButtonRelease-1>", self._on_release)

        def update_view(
            self,
            state: JSONValue,
            *,
            move_hints: set[int] | None = None,
            build_hints: set[int] | None = None,
            selected: set[int] | None = None,
        ) -> None:
            self._last_state = state
            self._move_hints = set(move_hints or set())
            self._build_hints = set(build_hints or set())
            self._selected = set(selected or set())
            self._redraw()

        def _point_to_cell(self, x: float, y: float) -> int | None:
            for idx, (x1, y1, x2, y2) in self._cell_boxes.items():
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return idx
            return None

        def _on_press(self, e: Any) -> None:
            cell = self._point_to_cell(float(e.x), float(e.y))
            if cell is None:
                return
            self._drag_start_cell = cell
            self._drag_start_xy = (float(e.x), float(e.y))
            self._dragging = False
            self._drag_cursor_xy = (float(e.x), float(e.y))

        def _on_motion(self, e: Any) -> None:
            if self._drag_start_cell is None or self._drag_start_xy is None:
                return
            sx, sy = self._drag_start_xy
            dx = float(e.x) - sx
            dy = float(e.y) - sy
            if (dx * dx + dy * dy) >= 100.0:
                self._dragging = True
            self._drag_cursor_xy = (float(e.x), float(e.y))
            if self._dragging:
                self._redraw()

        def _on_release(self, e: Any) -> None:
            start = self._drag_start_cell
            if start is None:
                return

            end = self._point_to_cell(float(e.x), float(e.y))
            if end is None:
                end = start

            is_piece_drag = self._dragging and start in self._piece_at and end != start

            self._drag_start_cell = None
            self._drag_start_xy = None
            self._dragging = False
            self._drag_cursor_xy = None

            if is_piece_drag:
                self._on_drag_drop(start, end)
            else:
                self._on_cell_click(end)
            self._redraw()

        def _redraw(self) -> None:
            self._canvas.delete("all")

            s = self._last_state if isinstance(self._last_state, dict) else {}
            board = list(s.get("board", [0] * 25))
            workers = s.get("workers", [[None, None], [None, None]])

            w = max(100, int(self._canvas.winfo_width()))
            h = max(100, int(self._canvas.winfo_height()))
            size = min(w, h) - 40
            cell = size / 5.0
            ox = (w - size) / 2.0
            oy = (h - size) / 2.0

            self._cell_boxes = {}
            self._piece_at = {}

            def cell_box(idx: int) -> tuple[float, float, float, float]:
                r, c = divmod(idx, 5)
                x1 = ox + c * cell
                y1 = oy + r * cell
                x2 = x1 + cell
                y2 = y1 + cell
                return (x1, y1, x2, y2)

            def cell_color(height: int) -> str:
                palette = {
                    0: "#dbe7f3",
                    1: "#a8c6e6",
                    2: "#79a6d2",
                    3: "#f5d97b",
                }
                if height >= 4:
                    return "#3b4757"
                return palette.get(height, "#dbe7f3")

            for i in range(25):
                x1, y1, x2, y2 = cell_box(i)
                self._cell_boxes[i] = (x1, y1, x2, y2)
                hval = int(board[i]) if i < len(board) and isinstance(board[i], int) else 0
                fill = cell_color(hval)

                outline = "#22384d"
                width = 2
                if i in self._move_hints:
                    outline = "#ff9f1c"
                    width = 4
                if i in self._build_hints:
                    outline = "#2ec4b6"
                    width = 4
                if i in self._selected:
                    outline = "#e71d36"
                    width = 5

                self._canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width)
                self._canvas.create_text(
                    x1 + 8,
                    y1 + 8,
                    text=str(hval if hval < 4 else "D"),
                    fill="#12263a" if hval < 4 else "#f1f5f9",
                    anchor="nw",
                    font=("Menlo", 9, "bold"),
                )

                if hval >= 4:
                    self._canvas.create_line(x1 + 8, y1 + 8, x2 - 8, y2 - 8, fill="#cbd5e1", width=2)
                    self._canvas.create_line(x2 - 8, y1 + 8, x1 + 8, y2 - 8, fill="#cbd5e1", width=2)

            piece_def = [
                (0, 0, "A", "#e63946"),
                (0, 1, "B", "#f77f00"),
                (1, 0, "a", "#4361ee"),
                (1, 1, "b", "#4cc9f0"),
            ]
            for pid, wid, label, color in piece_def:
                try:
                    pos = workers[pid][wid]
                except Exception:
                    pos = None
                if not isinstance(pos, int) or pos not in self._cell_boxes:
                    continue
                self._piece_at[int(pos)] = (pid, wid)
                x1, y1, x2, y2 = self._cell_boxes[int(pos)]
                mx = (x1 + x2) / 2.0
                my = (y1 + y2) / 2.0
                r = cell * 0.30
                self._canvas.create_oval(mx - r, my - r, mx + r, my + r, fill=color, outline="#0b132b", width=3)
                self._canvas.create_text(mx, my, text=label, fill="white", font=("Menlo", 14, "bold"))

            # Drag preview: show a ghost piece under the cursor.
            if self._dragging and self._drag_start_cell in self._piece_at and self._drag_cursor_xy is not None:
                pid, wid = self._piece_at[self._drag_start_cell]
                ghost_color = "#e63946" if (pid == 0 and wid == 0) else "#f77f00" if pid == 0 else "#4361ee" if wid == 0 else "#4cc9f0"
                gx, gy = self._drag_cursor_xy
                r = cell * 0.26
                self._canvas.create_oval(
                    gx - r,
                    gy - r,
                    gx + r,
                    gy + r,
                    fill=ghost_color,
                    outline="#0b132b",
                    width=2,
                    stipple="gray50",
                )

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
            self.configure(bg="#0f172a")

            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure("TFrame", background="#0f172a")
            style.configure("TLabelframe", background="#0f172a", foreground="#e2e8f0")
            style.configure("TLabelframe.Label", background="#0f172a", foreground="#e2e8f0")
            style.configure("TLabel", background="#0f172a", foreground="#e2e8f0")

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
            self._ss_build_sel: int | None = None
            self._ss_move_map: dict[tuple[int, int, int | None], JSONValue] = {}
            self._ss_dests_by_worker: dict[int, set[int]] = {}
            self._ss_builds_by_worker_dest: dict[tuple[int, int], set[int | None]] = {}

            # UI
            self._status_var = tk.StringVar(value="")
            self._action_var = tk.StringVar(value="")

            root = ttk.Frame(self, padding=12)
            root.pack(fill="both", expand=True)

            paned = ttk.Panedwindow(root, orient="horizontal")
            paned.pack(fill="both", expand=True)

            self._left = ttk.Frame(paned)
            self._right = ttk.Frame(paned)
            paned.add(self._left, weight=3)
            paned.add(self._right, weight=2)

            self._board_container = ttk.Frame(self._left)
            self._board_container.pack(fill="both", expand=True)

            legend = ttk.Frame(self._left, padding=(0, 8, 0, 0))
            legend.pack(fill="x")
            ttk.Label(legend, text="Move Target", foreground="#ff9f1c").pack(side="left")
            ttk.Label(legend, text="   Build Target", foreground="#2ec4b6").pack(side="left")
            ttk.Label(legend, text="   Selected", foreground="#e71d36").pack(side="left")

            self._board_kind: str = "text"
            self._board_widget: Any = None

            status = ttk.Label(self._right, textvariable=self._status_var, wraplength=360, justify="left")
            status.pack(fill="x", pady=(0, 8))

            action_card = ttk.LabelFrame(self._right, text="Action", padding=10)
            action_card.pack(fill="x", pady=(0, 10))
            ttk.Label(action_card, textvariable=self._action_var, wraplength=330, justify="left").pack(fill="x", pady=(0, 8))
            action_buttons = ttk.Frame(action_card)
            action_buttons.pack(fill="x")
            self._btn_build = ttk.Button(action_buttons, text="Build", command=self._confirm_skysummit_build)
            self._btn_cancel_action = ttk.Button(action_buttons, text="Cancel", command=self._cancel_skysummit_action)
            self._btn_build.grid(row=0, column=0, sticky="ew")
            self._btn_cancel_action.grid(row=0, column=1, sticky="ew", padx=(6, 0))
            action_buttons.columnconfigure(0, weight=1)
            action_buttons.columnconfigure(1, weight=1)

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
            self._hist = tk.Listbox(self._right, height=14)
            self._hist.configure(
                bg="#111827",
                fg="#e5e7eb",
                selectbackground="#334155",
                highlightthickness=1,
                highlightbackground="#334155",
                relief="flat",
            )
            self._hist.pack(fill="both", expand=False, pady=(4, 10))
            self._hist.bind("<<ListboxSelect>>", self._on_hist_select)

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
                self._board_widget = SkysummitBoard(
                    self._board_container,
                    on_cell_click=self._on_cell_click,
                    on_drag_drop=self._on_piece_drag_drop,
                )
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
            self._ss_build_sel = None
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
                        self._reset_skysummit_ui_state()
                        self._apply_live_move(move, ms=None, note=None)
                    else:
                        self._ss_place_sel = []
                        self._refresh()
                else:
                    self._refresh()
                return

            if phase != "play":
                return

            self._index_skysummit_moves(legal)

            worker_pos_by_idx: dict[int, int] = {}
            try:
                wpos = s.get("workers", [[None, None], [None, None]])[player]
                if isinstance(wpos, list) and len(wpos) == 2:
                    if isinstance(wpos[0], int):
                        worker_pos_by_idx[int(wpos[0])] = 0
                    if isinstance(wpos[1], int):
                        worker_pos_by_idx[int(wpos[1])] = 1
            except Exception:
                worker_pos_by_idx = {}

            # Clicking your worker selects it.
            if idx in worker_pos_by_idx:
                self._ss_worker_sel = worker_pos_by_idx[idx]
                self._ss_dest_sel = None
                self._ss_build_sel = None
                self._refresh()
                return

            # If a worker is selected but no destination yet, clicking a legal destination picks it.
            if self._ss_worker_sel is not None and self._ss_dest_sel is None:
                if self._select_skysummit_destination(self._ss_worker_sel, idx):
                    return
                self._refresh()
                return

            # Build selection after a destination is chosen.
            if self._ss_worker_sel is not None and self._ss_dest_sel is not None:
                builds = self._ss_builds_by_worker_dest.get((self._ss_worker_sel, self._ss_dest_sel), set())
                if idx in builds:
                    self._ss_build_sel = None if self._ss_build_sel == idx else idx
                    self._refresh()
                    return

                # Clicking another legal destination re-targets the move.
                if idx in self._ss_dests_by_worker.get(self._ss_worker_sel, set()):
                    if self._select_skysummit_destination(self._ss_worker_sel, idx):
                        return

            self._refresh()

        def _on_piece_drag_drop(self, src: int, dst: int) -> None:
            if self._busy or self._replay_mode:
                return
            if not self._is_at_latest():
                return
            if self._game is None:
                return

            if self._game.name != "skysummit":
                return

            player = self._player_to_act()
            agent = self._agents[player]
            if not _is_human(agent):
                return

            state = self._states[-1]
            legal = self._current_legal_moves()
            if legal is None:
                return

            s = state if isinstance(state, dict) else {}
            if s.get("phase") != "play":
                return

            self._index_skysummit_moves(legal)

            w = None
            try:
                wpos = s.get("workers", [[None, None], [None, None]])[player]
                if isinstance(wpos, list) and len(wpos) == 2:
                    if wpos[0] == src:
                        w = 0
                    elif wpos[1] == src:
                        w = 1
            except Exception:
                w = None

            if w is None:
                return

            self._ss_worker_sel = w
            self._ss_build_sel = None
            if self._select_skysummit_destination(w, dst):
                return
            self._refresh()

        def _select_skysummit_destination(self, worker_idx: int, dst: int) -> bool:
            if dst not in self._ss_dests_by_worker.get(worker_idx, set()):
                return False

            self._ss_worker_sel = worker_idx
            self._ss_dest_sel = dst
            self._ss_build_sel = None

            builds = self._ss_builds_by_worker_dest.get((worker_idx, dst), set())
            if None in builds:
                mv = self._ss_move_map.get((worker_idx, dst, None))
                if mv is not None:
                    self._reset_skysummit_ui_state()
                    self._apply_live_move(mv, ms=None, note=None)
                    return True

            self._refresh()
            return True

        def _confirm_skysummit_build(self) -> None:
            if self._busy or self._replay_mode or not self._is_at_latest():
                return
            if self._game is None or self._game.name != "skysummit":
                return
            player = self._player_to_act()
            if not _is_human(self._agents[player]):
                return
            if self._ss_worker_sel is None or self._ss_dest_sel is None or self._ss_build_sel is None:
                return

            mv = self._ss_move_map.get((self._ss_worker_sel, self._ss_dest_sel, self._ss_build_sel))
            if mv is None:
                return
            self._reset_skysummit_ui_state()
            self._apply_live_move(mv, ms=None, note=None)

        def _cancel_skysummit_action(self) -> None:
            self._reset_skysummit_ui_state()
            self._refresh()

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

            move_hints: set[int] = set()
            build_hints: set[int] = set()
            selected: set[int] = set()
            action_text = "Watch mode."
            can_build = False
            can_cancel = False

            if not self._replay_mode and self._is_at_latest() and not self._is_match_over():
                player = self._player_to_act()
                if _is_human(self._agents[player]):
                    legal = self._current_legal_moves()
                    if legal is not None:
                        if game.name == "tictactoe":
                            move_hints = set(int(x) for x in legal if isinstance(x, int))
                            action_text = "Your turn. Click a highlighted square."
                        elif game.name == "skysummit":
                            self._index_skysummit_moves(legal)
                            s = cur_state if isinstance(cur_state, dict) else {}
                            phase = s.get("phase")
                            if phase == "place":
                                selected = set(self._ss_place_sel)
                                can_cancel = bool(self._ss_place_sel)
                                action_text = "Placement: click two empty cells to deploy your two workers."
                                if len(self._ss_place_sel) == 1:
                                    action_text = "Placement: pick one more empty cell."
                            elif phase == "play":
                                can_cancel = (
                                    self._ss_worker_sel is not None
                                    or self._ss_dest_sel is not None
                                    or self._ss_build_sel is not None
                                )
                                wpos: list[int] = []
                                try:
                                    raw = s.get("workers", [[None, None], [None, None]])[player]
                                    if isinstance(raw, list):
                                        wpos = [int(x) for x in raw if isinstance(x, int)]
                                except Exception:
                                    wpos = []

                                if self._ss_worker_sel is None:
                                    move_hints = set(wpos)
                                    action_text = "Select a worker (or drag it) to start your move."
                                elif self._ss_dest_sel is None:
                                    if 0 <= self._ss_worker_sel < len(wpos):
                                        selected.add(int(wpos[self._ss_worker_sel]))
                                    move_hints = set(self._ss_dests_by_worker.get(self._ss_worker_sel, set()))
                                    action_text = "Choose a destination for the selected worker."
                                else:
                                    if 0 <= self._ss_worker_sel < len(wpos):
                                        selected.add(int(wpos[self._ss_worker_sel]))
                                    selected.add(int(self._ss_dest_sel))
                                    if self._ss_build_sel is not None:
                                        selected.add(int(self._ss_build_sel))
                                    raw_builds = self._ss_builds_by_worker_dest.get((self._ss_worker_sel, self._ss_dest_sel), set())
                                    build_hints = {int(x) for x in raw_builds if isinstance(x, int)}
                                    can_build = self._ss_build_sel is not None
                                    action_text = "Select a build cell, then press Build."
                                    if self._ss_build_sel is not None:
                                        action_text = "Build cell selected. Press Build to confirm."
                else:
                    action_text = "AI turn. Press Next or enable Autoplay."
            elif self._replay_mode:
                action_text = "Replay mode. Use Prev/Next or Autoplay."
            elif self._is_match_over():
                action_text = "Match over. Restart to play again."

            if self._board_kind == "skysummit":
                self._board_widget.update_view(
                    cur_state,
                    move_hints=move_hints,
                    build_hints=build_hints,
                    selected=selected,
                )
            elif self._board_kind == "tictactoe":
                self._board_widget.update_view(cur_state, highlights=move_hints, selected=selected)
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
            self._action_var.set(action_text)

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

            if self._board_kind == "skysummit":
                self._btn_build.configure(state=("normal" if can_build else "disabled"))
                self._btn_cancel_action.configure(state=("normal" if can_cancel else "disabled"))
            else:
                self._btn_build.configure(state="disabled")
                self._btn_cancel_action.configure(state="disabled")

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

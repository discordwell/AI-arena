"""Caldera tkinter GUI — interactive play & match replay."""

from __future__ import annotations

import json
import random as _random
import threading
import tkinter as tk
from pathlib import Path
from typing import Any, Callable

from opus.game.game import OpusGame, SIZE, VENT, CROWN, LANCER, SMITH

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

CELL_PX = 72
BOARD_PX = CELL_PX * SIZE

# Terrain colours keyed by height
TERRAIN_COLORS = {
    0: "#334155",  # cool slate
    1: "#78716c",  # warm stone
    2: "#ea580c",  # orange glow
    3: "#dc2626",  # red hot
}

VENT_BG = "#1a1a1a"
VENT_X = "#dc2626"

# Player colours
P_COLORS = {0: "#f59e0b", 1: "#3b82f6"}  # amber, blue

# Overlay colours
SEL_COLOR = "#facc15"    # selection highlight (yellow)
MOVE_COLOR = "#22c55e"   # move destination (green)
FORGE_COLOR = "#f97316"  # forge target (orange)
CAP_COLOR = "#ef4444"    # capturable enemy (red)

# Piece type → single letter
TYPE_LETTER = {CROWN: "C", LANCER: "L", SMITH: "S"}


# ---------------------------------------------------------------------------
# CalderaBoard — canvas widget
# ---------------------------------------------------------------------------

class CalderaBoard(tk.Canvas):
    """7x7 game board rendered on a tkinter Canvas."""

    def __init__(self, master: tk.Widget, on_cell_click: Callable[[int, int], None], **kw):
        super().__init__(
            master, width=BOARD_PX, height=BOARD_PX,
            bg="#0f172a", highlightthickness=0, **kw,
        )
        self._on_cell_click = on_cell_click
        self.bind("<Button-1>", self._handle_click)

        self._state: dict | None = None
        self._selected: tuple[int, int] | None = None
        self._move_targets: set[tuple[int, int]] = set()
        self._forge_targets: set[tuple[int, int]] = set()
        self._capture_targets: set[tuple[int, int]] = set()

    # -- public API -----------------------------------------------------

    def set_state(self, state: dict) -> None:
        self._state = state
        self._draw()

    def set_overlays(
        self,
        selected: tuple[int, int] | None = None,
        move_targets: set[tuple[int, int]] | None = None,
        forge_targets: set[tuple[int, int]] | None = None,
        capture_targets: set[tuple[int, int]] | None = None,
    ) -> None:
        self._selected = selected
        self._move_targets = move_targets or set()
        self._forge_targets = forge_targets or set()
        self._capture_targets = capture_targets or set()
        self._draw()

    def clear_overlays(self) -> None:
        self._selected = None
        self._move_targets = set()
        self._forge_targets = set()
        self._capture_targets = set()
        self._draw()

    # -- drawing --------------------------------------------------------

    def _draw(self) -> None:
        self.delete("all")
        if self._state is None:
            return

        board = self._state["board"]

        for r in range(SIZE):
            for c in range(SIZE):
                x0, y0 = c * CELL_PX, r * CELL_PX
                x1, y1 = x0 + CELL_PX, y0 + CELL_PX
                h = board[r][c]

                # Cell fill
                if h == VENT:
                    self.create_rectangle(x0, y0, x1, y1, fill=VENT_BG, outline="#333")
                    m = 12
                    self.create_line(x0 + m, y0 + m, x1 - m, y1 - m, fill=VENT_X, width=2)
                    self.create_line(x0 + m, y1 - m, x1 - m, y0 + m, fill=VENT_X, width=2)
                else:
                    fill = TERRAIN_COLORS.get(h, "#dc2626")
                    self.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#475569")
                    if h > 0:
                        self.create_text(
                            x1 - 10, y1 - 10, text=str(h),
                            fill="#ffffff99", font=("Helvetica", 9),
                        )

                # Overlay borders
                bw = 3
                if (r, c) == self._selected:
                    self.create_rectangle(
                        x0 + bw, y0 + bw, x1 - bw, y1 - bw,
                        outline=SEL_COLOR, width=bw,
                    )
                elif (r, c) in self._capture_targets:
                    self.create_rectangle(
                        x0 + bw, y0 + bw, x1 - bw, y1 - bw,
                        outline=CAP_COLOR, width=bw,
                    )
                elif (r, c) in self._forge_targets:
                    self.create_rectangle(
                        x0 + bw, y0 + bw, x1 - bw, y1 - bw,
                        outline=FORGE_COLOR, width=bw,
                    )
                elif (r, c) in self._move_targets:
                    self.create_rectangle(
                        x0 + bw, y0 + bw, x1 - bw, y1 - bw,
                        outline=MOVE_COLOR, width=bw,
                    )

        # Pieces
        for player in (0, 1):
            for p in self._state[f"p{player}"]:
                self._draw_piece(p, player)

    def _draw_piece(self, piece: dict, player: int) -> None:
        r, c = piece["r"], piece["c"]
        cx = c * CELL_PX + CELL_PX // 2
        cy = r * CELL_PX + CELL_PX // 2
        radius = CELL_PX // 2 - 10
        color = P_COLORS[player]
        letter = TYPE_LETTER[piece["type"]]

        self.create_oval(
            cx - radius, cy - radius, cx + radius, cy + radius,
            fill=color, outline="#fff", width=2,
        )
        self.create_text(cx, cy, text=letter, fill="#000", font=("Helvetica", 16, "bold"))

    # -- click handling -------------------------------------------------

    def _handle_click(self, event: tk.Event) -> None:
        c = event.x // CELL_PX
        r = event.y // CELL_PX
        if 0 <= r < SIZE and 0 <= c < SIZE:
            self._on_cell_click(r, c)


# ---------------------------------------------------------------------------
# CalderaApp — main application
# ---------------------------------------------------------------------------

class CalderaApp:
    """Top-level GUI for Caldera — play or replay mode."""

    def __init__(
        self,
        agents: dict[int, str],
        replay_path: str | None = None,
    ):
        self.game = OpusGame()
        self.root = tk.Tk()
        self.root.title("Caldera \u2014 Opus Arena")
        self.root.configure(bg="#1e293b")
        self.root.resizable(False, False)

        self._agents_cfg = agents
        self._agents: dict[int, Any] = {}
        self._replay_path = replay_path
        self._replay_states: list[dict] | None = None
        self._replay_cursor = 0
        self._replay_moves: list[dict] | None = None

        self._state: dict = self.game.initial_state()
        self._current_player: int = 0
        self._selected: tuple[int, int] | None = None
        self._legal_moves: list[dict] = []
        self._human_waiting = False
        self._game_over = False
        self._generation = 0          # bumped on new game; guards stale AI callbacks
        self._closing = False
        self._autoplay_id: str | None = None
        self._move_history: list[str] = []

        self._build_ui()

        if replay_path:
            self._init_replay(replay_path)
        else:
            self._init_agents()
            self._start_turn()

    # -- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        main = tk.Frame(self.root, bg="#1e293b")
        main.pack(fill=tk.BOTH, expand=True)

        # Left: board
        self.board = CalderaBoard(main, on_cell_click=self._on_cell_click)
        self.board.pack(side=tk.LEFT, padx=8, pady=8)

        # Right panel
        right = tk.Frame(main, bg="#1e293b", width=260)
        right.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8), pady=8)
        right.pack_propagate(False)

        # Status label
        self.status_var = tk.StringVar(value="Caldera")
        tk.Label(
            right, textvariable=self.status_var, bg="#1e293b", fg="#e2e8f0",
            font=("Helvetica", 13, "bold"), wraplength=240, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 6))

        # Info label (ply, piece counts)
        self.info_var = tk.StringVar(value="")
        tk.Label(
            right, textvariable=self.info_var, bg="#1e293b", fg="#94a3b8",
            font=("Helvetica", 11), wraplength=240, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        # Action hint
        self.action_var = tk.StringVar(value="")
        tk.Label(
            right, textvariable=self.action_var, bg="#1e293b", fg="#fbbf24",
            font=("Helvetica", 10), wraplength=240, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        # Controls frame
        self._ctrl_frame = tk.Frame(right, bg="#1e293b")
        self._ctrl_frame.pack(anchor=tk.W, pady=(0, 10))

        if self._replay_path:
            self._build_replay_controls()
        else:
            self._build_play_controls()

        # Move history
        tk.Label(
            right, text="Move History", bg="#1e293b", fg="#94a3b8",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor=tk.W, pady=(6, 2))

        hist_frame = tk.Frame(right, bg="#1e293b")
        hist_frame.pack(fill=tk.BOTH, expand=True)

        self.history_list = tk.Listbox(
            hist_frame, bg="#0f172a", fg="#cbd5e1",
            selectbackground="#334155", font=("Courier", 9),
            borderwidth=0, highlightthickness=0,
        )
        scrollbar = tk.Scrollbar(hist_frame, command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=scrollbar.set)
        self.history_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Legend bar
        legend = tk.Frame(self.root, bg="#0f172a", height=28)
        legend.pack(fill=tk.X)
        legend_text = (
            "C=Crown  L=Lancer  S=Smith  |  "
            "Green=Move  Orange=Forge  Red=Capture  |  "
            "Esc=Deselect"
        )
        tk.Label(
            legend, text=legend_text, bg="#0f172a", fg="#64748b",
            font=("Helvetica", 9),
        ).pack(pady=3)

        # Key bindings
        self.root.bind("<Escape>", lambda e: self._deselect())

    def _build_play_controls(self) -> None:
        btn_cfg = dict(
            bg="#334155", fg="#e2e8f0", font=("Helvetica", 10),
            activebackground="#475569", activeforeground="#fff",
            borderwidth=0, padx=8, pady=4,
        )
        tk.Button(
            self._ctrl_frame, text="New Game", command=self._new_game, **btn_cfg,
        ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_replay_controls(self) -> None:
        btn_cfg = dict(
            bg="#334155", fg="#e2e8f0", font=("Helvetica", 10),
            activebackground="#475569", activeforeground="#fff",
            borderwidth=0, padx=8, pady=4,
        )
        tk.Button(
            self._ctrl_frame, text="<< Start", command=self._replay_start, **btn_cfg,
        ).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(
            self._ctrl_frame, text="< Prev", command=self._replay_prev, **btn_cfg,
        ).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(
            self._ctrl_frame, text="Next >", command=self._replay_next, **btn_cfg,
        ).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(
            self._ctrl_frame, text="End >>", command=self._replay_end, **btn_cfg,
        ).pack(side=tk.LEFT, padx=(0, 2))
        self._autoplay_btn = tk.Button(
            self._ctrl_frame, text="Auto \u25b6", command=self._replay_toggle_auto, **btn_cfg,
        )
        self._autoplay_btn.pack(side=tk.LEFT, padx=(4, 0))

    # -- agent initialisation -------------------------------------------

    def _init_agents(self) -> None:
        for pid, kind in self._agents_cfg.items():
            if kind == "human":
                self._agents[pid] = "human"
            elif kind == "random":
                self._agents[pid] = "random"
            elif kind == "opus":
                from opus.agent.agent import OpusAgent
                self._agents[pid] = OpusAgent()
            else:
                self._agents[pid] = "random"

    def _close_agents(self) -> None:
        for a in self._agents.values():
            if hasattr(a, "close"):
                a.close()

    # -- game flow (play mode) ------------------------------------------

    def _start_turn(self) -> None:
        if self._game_over:
            return

        term = self.game.terminal(self._state)
        if term.is_terminal:
            self._game_over = True
            winner_str = f"P{term.winner}" if term.winner is not None else "Draw"
            self.status_var.set(f"Game Over \u2014 {winner_str} ({term.reason})")
            self.action_var.set("")
            self.board.clear_overlays()
            return

        self._current_player = self._state["ply"] % 2
        self._legal_moves = self.game.legal_moves(self._state, self._current_player)
        self._update_info()

        # No legal moves → forfeit
        if not self._legal_moves:
            self._game_over = True
            winner = 1 - self._current_player
            self.status_var.set(f"Game Over \u2014 P{winner} (no legal moves)")
            self.action_var.set("")
            self.board.clear_overlays()
            return

        agent = self._agents.get(self._current_player, "human")

        if agent == "human":
            self._human_waiting = True
            self.status_var.set(f"Your turn (P{self._current_player})")
            self.action_var.set("Click a piece to select it")
        elif agent == "random":
            self._human_waiting = False
            self.status_var.set(f"P{self._current_player} (random) thinking...")
            self.action_var.set("")
            self.root.after(300, self._do_random_move)
        else:
            # AI agent — run in background thread
            self._human_waiting = False
            gen = self._generation
            self.status_var.set(f"P{self._current_player} (AI) thinking...")
            self.action_var.set("")
            self.board.clear_overlays()
            t = threading.Thread(
                target=self._ai_worker, args=(agent, gen), daemon=True,
            )
            t.start()

    def _do_random_move(self) -> None:
        if not self._legal_moves:
            return
        move = _random.choice(self._legal_moves)
        self._execute_move(move)

    def _ai_worker(self, agent: Any, gen: int) -> None:
        try:
            move = agent.select_move(
                self.game, self._state, self._current_player, self._legal_moves,
            )
        except Exception:
            move = _random.choice(self._legal_moves) if self._legal_moves else None
        self.root.after(0, lambda: self._ai_move_ready(move, gen))

    def _ai_move_ready(self, move: dict | None, gen: int) -> None:
        if self._closing or gen != self._generation:
            return  # stale callback from a previous game or shutting down
        if move:
            self._execute_move(move)

    def _execute_move(self, move: dict) -> None:
        desc = self._describe_move(move, self._current_player)
        self._move_history.append(desc)
        self.history_list.insert(tk.END, desc)
        self.history_list.see(tk.END)

        self._state = self.game.apply_move(self._state, self._current_player, move)
        self._selected = None
        self.board.clear_overlays()
        self.board.set_state(self._state)
        self._start_turn()

    def _describe_move(self, move: dict, player: int) -> str:
        ply = self._state["ply"]
        tag = f"P{player}"
        if move["action"] == "move":
            fr = move["from"]
            to = move["to"]
            ptype = "?"
            for p in self._state[f"p{player}"]:
                if p["r"] == fr[0] and p["c"] == fr[1]:
                    ptype = TYPE_LETTER[p["type"]]
                    break
            cap = ""
            opp = 1 - player
            for p in self._state[f"p{opp}"]:
                if p["r"] == to[0] and p["c"] == to[1]:
                    cap = "x"
                    break
            return f"{ply:>3}. {tag} {ptype} {fr[0]},{fr[1]}{cap}\u2192{to[0]},{to[1]}"
        elif move["action"] == "forge":
            t = move["target"]
            return f"{ply:>3}. {tag} S forge {t[0]},{t[1]}"
        return f"{ply:>3}. {tag} {move}"

    # -- human interaction ----------------------------------------------

    def _on_cell_click(self, r: int, c: int) -> None:
        if not self._human_waiting:
            return

        if self._selected is not None:
            sr, sc = self._selected
            # Check for move/forge/capture at (r, c)
            for m in self._legal_moves:
                if m["action"] == "move" and m["from"] == [sr, sc] and m["to"] == [r, c]:
                    self._human_waiting = False
                    self._execute_move(m)
                    return
                if m["action"] == "forge" and m["smith"] == [sr, sc] and m["target"] == [r, c]:
                    self._human_waiting = False
                    self._execute_move(m)
                    return

            # Clicked another friendly piece → switch selection
            for p in self._state[f"p{self._current_player}"]:
                if p["r"] == r and p["c"] == c:
                    self._select_piece(r, c)
                    return

            # Clicked empty / invalid → deselect
            self._deselect()
            return

        # No current selection — try to select a piece
        for p in self._state[f"p{self._current_player}"]:
            if p["r"] == r and p["c"] == c:
                self._select_piece(r, c)
                return

    def _select_piece(self, r: int, c: int) -> None:
        self._selected = (r, c)

        move_targets: set[tuple[int, int]] = set()
        forge_targets: set[tuple[int, int]] = set()
        capture_targets: set[tuple[int, int]] = set()

        opp = 1 - self._current_player
        enemy_pos = {(p["r"], p["c"]) for p in self._state[f"p{opp}"]}

        for m in self._legal_moves:
            if m["action"] == "move" and m["from"] == [r, c]:
                dest = (m["to"][0], m["to"][1])
                if dest in enemy_pos:
                    capture_targets.add(dest)
                else:
                    move_targets.add(dest)
            elif m["action"] == "forge" and m["smith"] == [r, c]:
                forge_targets.add((m["target"][0], m["target"][1]))

        self.board.set_overlays(
            selected=(r, c),
            move_targets=move_targets,
            forge_targets=forge_targets,
            capture_targets=capture_targets,
        )

        ptype = "?"
        for p in self._state[f"p{self._current_player}"]:
            if p["r"] == r and p["c"] == c:
                ptype = p["type"].capitalize()
                break
        n = len(move_targets) + len(forge_targets) + len(capture_targets)
        self.action_var.set(f"{ptype} selected \u2014 {n} actions available")

    def _deselect(self) -> None:
        self._selected = None
        self.board.clear_overlays()
        if self._human_waiting:
            self.action_var.set("Click a piece to select it")

    # -- replay mode ----------------------------------------------------

    def _init_replay(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        result = data.get("result", data)
        moves = result.get("move_history", [])

        # Reconstruct all states from initial by applying each move
        states = [self.game.initial_state()]
        for rec in moves:
            player = rec["player"]
            move = rec["move"]
            if move is None:
                break
            states.append(self.game.apply_move(states[-1], player, move))

        self._replay_states = states
        self._replay_moves = moves
        self._replay_cursor = 0
        self._show_replay_state()

    def _show_replay_state(self) -> None:
        if self._replay_states is None:
            return

        idx = self._replay_cursor
        total = len(self._replay_states) - 1
        st = self._replay_states[idx]

        self.board.clear_overlays()
        self.board.set_state(st)
        self._update_info(st)

        if idx == 0:
            self.status_var.set(f"Replay \u2014 Start (0/{total})")
            self.action_var.set("")
        else:
            rec = self._replay_moves[idx - 1]
            desc = self._describe_replay_move(rec, idx)
            self.status_var.set(f"Replay \u2014 Move {idx}/{total}")
            self.action_var.set(desc)

        # Sync history list
        self.history_list.delete(0, tk.END)
        for i in range(min(idx, len(self._replay_moves))):
            rec = self._replay_moves[i]
            self.history_list.insert(tk.END, self._describe_replay_move(rec, i + 1))
        if self.history_list.size() > 0:
            self.history_list.see(tk.END)

        term = self.game.terminal(st)
        if term.is_terminal:
            winner_str = f"P{term.winner}" if term.winner is not None else "Draw"
            self.status_var.set(f"Replay \u2014 Final \u2014 {winner_str} ({term.reason})")

    def _describe_replay_move(self, rec: dict, idx: int) -> str:
        m = rec["move"]
        p = rec["player"]
        tag = f"P{p}"
        if m is None:
            return f"{idx:>3}. {tag} (no move)"
        if m["action"] == "move":
            fr, to = m["from"], m["to"]
            return f"{idx:>3}. {tag} {fr[0]},{fr[1]}\u2192{to[0]},{to[1]}"
        elif m["action"] == "forge":
            t = m["target"]
            return f"{idx:>3}. {tag} forge {t[0]},{t[1]}"
        return f"{idx:>3}. {tag} {m}"

    def _replay_next(self) -> None:
        if self._replay_states and self._replay_cursor < len(self._replay_states) - 1:
            self._replay_cursor += 1
            self._show_replay_state()

    def _replay_prev(self) -> None:
        if self._replay_states and self._replay_cursor > 0:
            self._replay_cursor -= 1
            self._show_replay_state()

    def _replay_start(self) -> None:
        self._replay_cursor = 0
        self._show_replay_state()

    def _replay_end(self) -> None:
        if self._replay_states:
            self._replay_cursor = len(self._replay_states) - 1
            self._show_replay_state()

    def _replay_toggle_auto(self) -> None:
        if self._autoplay_id is not None:
            self.root.after_cancel(self._autoplay_id)
            self._autoplay_id = None
            self._autoplay_btn.configure(text="Auto \u25b6")
        else:
            self._autoplay_btn.configure(text="Stop \u25a0")
            self._autoplay_step()

    def _autoplay_step(self) -> None:
        if self._replay_states and self._replay_cursor < len(self._replay_states) - 1:
            self._replay_cursor += 1
            self._show_replay_state()
            self._autoplay_id = self.root.after(600, self._autoplay_step)
        else:
            self._autoplay_id = None
            self._autoplay_btn.configure(text="Auto \u25b6")

    # -- helpers --------------------------------------------------------

    def _update_info(self, state: dict | None = None) -> None:
        st = state or self._state
        n0 = len(st["p0"])
        n1 = len(st["p1"])
        ply = st["ply"]
        self.info_var.set(f"Ply {ply}  |  P0: {n0} pieces  |  P1: {n1} pieces")

    def _new_game(self) -> None:
        self._generation += 1  # invalidate any in-flight AI callback
        self._close_agents()
        self._state = self.game.initial_state()
        self._current_player = 0
        self._selected = None
        self._legal_moves = []
        self._human_waiting = False
        self._game_over = False
        self._move_history = []
        self.history_list.delete(0, tk.END)
        self.board.clear_overlays()
        self.board.set_state(self._state)
        self._init_agents()
        self._start_turn()

    def run(self) -> None:
        self.board.set_state(self._state)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        self._closing = True
        self._generation += 1  # invalidate any in-flight AI callback
        if self._autoplay_id is not None:
            self.root.after_cancel(self._autoplay_id)
        self._close_agents()
        self.root.destroy()

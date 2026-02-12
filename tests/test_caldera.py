"""Tests for Caldera (Opus home game)."""
from __future__ import annotations

import copy

import pytest

from opus.game.game import OpusGame, CROWN, LANCER, SMITH, VENT, SIZE


@pytest.fixture
def game() -> OpusGame:
    return OpusGame()


@pytest.fixture
def state(game: OpusGame):
    return game.initial_state()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_board_all_zeros(self, state):
        for r in range(SIZE):
            for c in range(SIZE):
                assert state["board"][r][c] == 0

    def test_piece_counts(self, state):
        assert len(state["p0"]) == 5
        assert len(state["p1"]) == 5

    def test_piece_types(self, state):
        for key in ("p0", "p1"):
            types = [p["type"] for p in state[key]]
            assert types.count(CROWN) == 1
            assert types.count(LANCER) == 2
            assert types.count(SMITH) == 2

    def test_p0_on_row_6(self, state):
        for p in state["p0"]:
            assert p["r"] == 6

    def test_p1_on_row_0(self, state):
        for p in state["p1"]:
            assert p["r"] == 0

    def test_symmetry(self, state):
        """P0 and P1 pieces mirror across the board center."""
        for p0, p1 in zip(state["p0"], state["p1"]):
            assert p0["type"] == p1["type"]
            assert p0["c"] == p1["c"]
            assert p0["r"] + p1["r"] == 6  # mirror across center

    def test_not_terminal(self, game, state):
        t = game.terminal(state)
        assert not t.is_terminal


# ---------------------------------------------------------------------------
# Legal moves  --  initial position
# ---------------------------------------------------------------------------

class TestLegalMovesInitial:
    def test_p0_has_moves(self, game, state):
        moves = game.legal_moves(state, 0)
        assert len(moves) > 0

    def test_p1_has_moves(self, game, state):
        moves = game.legal_moves(state, 1)
        assert len(moves) > 0

    def test_no_moves_off_board(self, game, state):
        for m in game.legal_moves(state, 0):
            if m["action"] == "move":
                r, c = m["to"]
                assert 0 <= r < SIZE and 0 <= c < SIZE
            elif m["action"] == "forge":
                r, c = m["target"]
                assert 0 <= r < SIZE and 0 <= c < SIZE

    def test_no_friendly_captures(self, game, state):
        """No move should land on a friendly piece."""
        friendly_pos = {(p["r"], p["c"]) for p in state["p0"]}
        for m in game.legal_moves(state, 0):
            if m["action"] == "move":
                assert tuple(m["to"]) not in friendly_pos

    def test_forge_targets_are_empty(self, game, state):
        all_pos = {(p["r"], p["c"]) for p in state["p0"] + state["p1"]}
        for m in game.legal_moves(state, 0):
            if m["action"] == "forge":
                assert tuple(m["target"]) not in all_pos

    def test_lancer_has_2step_moves(self, game, state):
        """Lancers on the open board should have 2-step moves."""
        moves = game.legal_moves(state, 0)
        lancer_pos = [(p["r"], p["c"]) for p in state["p0"] if p["type"] == LANCER]
        two_step = [
            m for m in moves
            if m["action"] == "move"
            and tuple(m["from"]) in [(lp[0], lp[1]) for lp in lancer_pos]
            and (abs(m["to"][0] - m["from"][0]) == 2 or abs(m["to"][1] - m["from"][1]) == 2)
        ]
        assert len(two_step) > 0


# ---------------------------------------------------------------------------
# Movement and capture
# ---------------------------------------------------------------------------

class TestMovement:
    def test_crown_basic_move(self, game, state):
        # Move P0 crown (6,3) north to (5,3)
        move = {"action": "move", "from": [6, 3], "to": [5, 3]}
        s2 = game.apply_move(state, 0, move)
        crown = [p for p in s2["p0"] if p["type"] == CROWN][0]
        assert crown["r"] == 5 and crown["c"] == 3
        assert s2["ply"] == 1

    def test_capture_removes_piece(self, game):
        """Place an enemy piece adjacent to a friendly piece and capture it."""
        state = game.initial_state()
        # Move a P0 lancer directly adjacent to a P1 piece
        # Manually set up: P0 lancer at (2,1), P1 lancer at (1,1)
        state["p0"][1]["r"] = 2  # lancer at row 2
        state["p1"][1]["r"] = 1  # enemy lancer at row 1

        move = {"action": "move", "from": [2, 1], "to": [1, 1]}
        s2 = game.apply_move(state, 0, move)
        assert len(s2["p1"]) == 4  # one captured
        assert len(s2["p0"]) == 5  # all intact

    def test_crown_capture_wins(self, game):
        state = game.initial_state()
        # Place P0 lancer adjacent to P1 crown
        state["p0"][1]["r"] = 1
        state["p0"][1]["c"] = 3  # next to P1 crown at (0,3)

        move = {"action": "move", "from": [1, 3], "to": [0, 3]}
        s2 = game.apply_move(state, 0, move)
        assert s2["winner"] == 0
        assert s2["reason"] == "crown_captured"
        assert game.terminal(s2).is_terminal

    def test_cannot_climb_more_than_1(self, game):
        state = game.initial_state()
        # Build a height-2 wall
        state["board"][5][3] = 2
        moves = game.legal_moves(state, 0)
        # Crown at (6,3) on height 0 should NOT be able to move to (5,3) at height 2
        crown_to_53 = [m for m in moves if m["action"] == "move"
                       and m["from"] == [6, 3] and m["to"] == [5, 3]]
        assert len(crown_to_53) == 0

    def test_can_descend_any(self, game):
        state = game.initial_state()
        state["board"][6][3] = 3  # Crown on height 3
        state["board"][5][3] = 0  # Adjacent at height 0
        moves = game.legal_moves(state, 0)
        crown_down = [m for m in moves if m["action"] == "move"
                      and m["from"] == [6, 3] and m["to"] == [5, 3]]
        assert len(crown_down) == 1

    def test_cannot_move_onto_vent(self, game):
        state = game.initial_state()
        state["board"][5][3] = VENT
        moves = game.legal_moves(state, 0)
        onto_vent = [m for m in moves if m["action"] == "move" and m["to"] == [5, 3]]
        assert len(onto_vent) == 0


# ---------------------------------------------------------------------------
# Lancer leap
# ---------------------------------------------------------------------------

class TestLancerLeap:
    def test_leap_over_piece(self, game):
        state = game.initial_state()
        # Place P0 lancer at (4,3) and a blocking piece at (3,3)
        state["p0"][1]["r"] = 4
        state["p0"][1]["c"] = 3
        state["p1"][0]["r"] = 3  # enemy crown at (3,3)
        state["p1"][0]["c"] = 3

        moves = game.legal_moves(state, 0)
        # Lancer at (4,3) should be able to leap to (2,3) over the piece at (3,3)
        leap = [m for m in moves if m["action"] == "move"
                and m["from"] == [4, 3] and m["to"] == [2, 3]]
        assert len(leap) == 1

    def test_no_leap_over_vent(self, game):
        state = game.initial_state()
        state["p0"][1]["r"] = 4
        state["p0"][1]["c"] = 3
        state["board"][3][3] = VENT  # vent at intermediate cell

        moves = game.legal_moves(state, 0)
        leap = [m for m in moves if m["action"] == "move"
                and m["from"] == [4, 3] and m["to"] == [2, 3]]
        assert len(leap) == 0

    def test_leap_height_constraint(self, game):
        state = game.initial_state()
        state["p0"][1]["r"] = 4
        state["p0"][1]["c"] = 3
        # Origin at height 0, intermediate at height 2 -> too high to climb
        state["board"][3][3] = 2

        moves = game.legal_moves(state, 0)
        leap = [m for m in moves if m["action"] == "move"
                and m["from"] == [4, 3] and m["to"] == [2, 3]]
        assert len(leap) == 0


# ---------------------------------------------------------------------------
# Forge and eruptions
# ---------------------------------------------------------------------------

class TestForge:
    def test_forge_raises_height(self, game):
        state = game.initial_state()
        # Smith at (6,2), forge target (5,2) - north
        move = {"action": "forge", "smith": [6, 2], "target": [5, 2]}
        s2 = game.apply_move(state, 0, move)
        assert s2["board"][5][2] == 1

    def test_forge_only_empty_cells(self, game):
        state = game.initial_state()
        moves = game.legal_moves(state, 0)
        for m in moves:
            if m["action"] == "forge":
                tr, tc = m["target"]
                all_pos = {(p["r"], p["c"]) for p in state["p0"] + state["p1"]}
                assert (tr, tc) not in all_pos


class TestEruption:
    def test_single_eruption(self, game):
        state = game.initial_state()
        state["board"][5][2] = 3  # one forge away from eruption
        move = {"action": "forge", "smith": [6, 2], "target": [5, 2]}
        s2 = game.apply_move(state, 0, move)
        assert s2["board"][5][2] == VENT

    def test_eruption_raises_neighbors(self, game):
        state = game.initial_state()
        state["board"][3][3] = 3  # will erupt when raised

        # Put a smith at (3,2) to forge (3,3) -> but (3,3) has no piece, good
        # Actually need smith adjacent to (3,3) orthogonally
        state["p0"][3]["r"] = 3
        state["p0"][3]["c"] = 2  # smith at (3,2)

        move = {"action": "forge", "smith": [3, 2], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)

        assert s2["board"][3][3] == VENT
        # Neighbors should have been raised by 1
        assert s2["board"][2][3] == 1  # north
        assert s2["board"][4][3] == 1  # south
        assert s2["board"][3][4] == 1  # east
        # (3,2) has a piece on it, height still raises
        assert s2["board"][3][2] == 1  # west (smith is there but height still rises)

    def test_chain_eruption(self, game):
        state = game.initial_state()
        # Set up two adjacent height-3 cells
        state["board"][3][3] = 3
        state["board"][3][4] = 3

        # Put smith at (3,2) to forge (3,3)
        state["p0"][3]["r"] = 3
        state["p0"][3]["c"] = 2

        move = {"action": "forge", "smith": [3, 2], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)

        # Both should be vents now
        assert s2["board"][3][3] == VENT
        assert s2["board"][3][4] == VENT  # chain erupted

    def test_eruption_destroys_piece(self, game):
        state = game.initial_state()
        state["board"][3][3] = 3
        # Place enemy piece ON an adjacent cell that will be raised
        state["board"][3][4] = 3  # this will chain
        state["p1"][1]["r"] = 3
        state["p1"][1]["c"] = 4  # enemy lancer on (3,4)

        state["p0"][3]["r"] = 3
        state["p0"][3]["c"] = 2

        move = {"action": "forge", "smith": [3, 2], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)

        assert s2["board"][3][4] == VENT
        # Enemy lancer should be destroyed
        assert len(s2["p1"]) == 4

    def test_eruption_kills_crown(self, game):
        state = game.initial_state()
        state["board"][3][3] = 3
        state["board"][3][4] = 3
        # Put enemy crown on cell that will chain-erupt
        state["p1"][0]["r"] = 3
        state["p1"][0]["c"] = 4

        state["p0"][3]["r"] = 3
        state["p0"][3]["c"] = 2

        move = {"action": "forge", "smith": [3, 2], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)

        assert s2["winner"] == 0
        assert "erupted" in s2["reason"]

    def test_mutual_crown_eruption_active_player_loses(self, game):
        state = game.initial_state()
        state["board"][3][3] = 3
        state["board"][3][4] = 3
        state["board"][3][2] = 3  # this will also chain

        # Both crowns on eruption cells
        state["p0"][0]["r"] = 3
        state["p0"][0]["c"] = 2
        state["p1"][0]["r"] = 3
        state["p1"][0]["c"] = 4

        # Smith at (2,3) forges (3,3)
        state["p0"][3]["r"] = 2
        state["p0"][3]["c"] = 3

        move = {"action": "forge", "smith": [2, 3], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)

        # Active player (0) should lose since they caused mutual destruction
        assert s2["winner"] == 1
        assert "mutual" in s2["reason"]


# ---------------------------------------------------------------------------
# Terminal conditions
# ---------------------------------------------------------------------------

class TestTerminal:
    def test_turn_limit_draw(self, game, state):
        state["ply"] = 200
        t = game.terminal(state)
        assert t.is_terminal
        assert t.reason == "turn_limit_draw"

    def test_turn_limit_more_pieces_wins(self, game, state):
        state["ply"] = 200
        state["p0"] = state["p0"][:4]  # remove one P0 piece
        t = game.terminal(state)
        assert t.is_terminal
        assert t.winner == 1

    def test_no_legal_moves_engine_handles(self, game):
        """The engine handles 'no legal moves' -> opponent wins.
        We just verify legal_moves returns [] in such a scenario."""
        state = game.initial_state()
        # Surround P0 crown with vents and friendly pieces
        state["board"][5][2] = VENT
        state["board"][5][3] = VENT
        state["board"][5][4] = VENT
        # Remove all P0 pieces except crown
        state["p0"] = [state["p0"][0]]  # only crown at (6,3)
        # Block edges with vents
        state["board"][6][2] = VENT
        state["board"][6][4] = VENT

        moves = game.legal_moves(state, 0)
        # Crown might still be able to move to (6,2) etc -- depends on vent placement
        # The point is: if trapped, legal_moves returns []
        # Let's fully surround:
        for dr, dc in [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]:
            nr, nc = 6 + dr, 3 + dc
            if 0 <= nr < SIZE and 0 <= nc < SIZE:
                state["board"][nr][nc] = VENT

        moves = game.legal_moves(state, 0)
        assert moves == []


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_initial(self, game, state):
        txt = game.render(state)
        assert "Caldera" in txt
        assert "ply=0" in txt
        assert "P0(5)" in txt
        assert "P1(5)" in txt
        # Check piece symbols
        assert "c" in txt  # P0 crown
        assert "C" in txt  # P1 crown

    def test_render_vent(self, game, state):
        state["board"][3][3] = VENT
        txt = game.render(state)
        assert "X" in txt


# ---------------------------------------------------------------------------
# Full game simulation (random vs random)
# ---------------------------------------------------------------------------

class TestAdditionalEdgeCases:
    def test_lancer_1step_move(self, game):
        """Lancers should have 1-step moves alongside 2-step moves."""
        state = game.initial_state()
        state["p0"][1]["r"] = 4  # lancer at (4,1)
        state["p0"][1]["c"] = 1
        moves = game.legal_moves(state, 0)
        one_step = [m for m in moves if m["action"] == "move"
                    and m["from"] == [4, 1]
                    and abs(m["to"][0] - 4) <= 1 and abs(m["to"][1] - 1) <= 1]
        assert len(one_step) > 0

    def test_lancer_capture_via_leap(self, game):
        """Lancer should capture an enemy piece via 2-step leap."""
        state = game.initial_state()
        state["p0"][1]["r"] = 4
        state["p0"][1]["c"] = 3
        # Blocker at (3,3), enemy at (2,3)
        state["p1"][1]["r"] = 3
        state["p1"][1]["c"] = 3
        state["p1"][2]["r"] = 2
        state["p1"][2]["c"] = 3  # enemy lancer at (2,3)

        move = {"action": "move", "from": [4, 3], "to": [2, 3]}
        s2 = game.apply_move(state, 0, move)
        assert len(s2["p1"]) == 4  # one captured

    def test_eruption_destroys_forging_smith(self, game):
        """Chain eruption can circle back and destroy the Smith that forged."""
        state = game.initial_state()
        # Set up heights so eruption cascades back to smith
        state["board"][3][3] = 3
        state["board"][3][2] = 3  # will chain and create vent on smith's cell
        state["board"][3][1] = 3  # chain continues

        # Smith at (3,4), forging (3,3)
        state["p0"][3]["r"] = 3
        state["p0"][3]["c"] = 4

        # Place smith at risk: put a P0 piece at (3,1)
        state["p0"][4]["r"] = 3
        state["p0"][4]["c"] = 1  # this smith will be on erupting cell

        move = {"action": "forge", "smith": [3, 4], "target": [3, 3]}
        s2 = game.apply_move(state, 0, move)
        # (3,3) erupts, raises (3,2) to 4, which erupts, raises (3,1) to 4, which erupts
        assert s2["board"][3][1] == VENT
        # Smith at (3,1) should be destroyed
        pos_alive = {(p["r"], p["c"]) for p in s2["p0"]}
        assert (3, 1) not in pos_alive

    def test_crown_height_tiebreak(self, game, state):
        """At turn limit with equal pieces, higher crown wins."""
        state["ply"] = 200
        state["board"][6][3] = 2  # P0 crown at height 2
        state["board"][0][3] = 1  # P1 crown at height 1
        t = game.terminal(state)
        assert t.is_terminal
        assert t.winner == 0
        assert t.reason == "turn_limit_crown_height"

    def test_apply_move_does_not_mutate_input(self, game, state):
        """apply_move should return a new state without modifying the input."""
        import copy
        original = copy.deepcopy(state)
        move = game.legal_moves(state, 0)[0]
        game.apply_move(state, 0, move)
        assert state == original

    def test_legal_moves_empty_on_terminal(self, game, state):
        """legal_moves returns [] when winner is already set."""
        state["winner"] = 0
        state["reason"] = "test"
        assert game.legal_moves(state, 0) == []
        assert game.legal_moves(state, 1) == []

    def test_legal_moves_empty_at_ply_limit(self, game, state):
        """legal_moves returns [] when ply >= MAX_PLY."""
        state["ply"] = 200
        assert game.legal_moves(state, 0) == []

    def test_forge_rejects_vent_cells(self, game):
        """Cannot forge a cell that is already a vent."""
        state = game.initial_state()
        state["board"][5][2] = VENT  # vent north of smith at (6,2)
        moves = game.legal_moves(state, 0)
        forge_to_vent = [m for m in moves if m["action"] == "forge"
                         and m["target"] == [5, 2]]
        assert len(forge_to_vent) == 0


class TestRandomGame:
    def test_random_game_terminates(self, game):
        """A game with random moves should eventually terminate."""
        import random
        random.seed(42)

        state = game.initial_state()
        player = 0
        for _ in range(300):
            t = game.terminal(state)
            if t.is_terminal:
                break
            moves = game.legal_moves(state, player)
            if not moves:
                break
            move = random.choice(moves)
            state = game.apply_move(state, player, move)
            player = 1 - player
        else:
            pytest.fail("game did not terminate within 300 iterations")

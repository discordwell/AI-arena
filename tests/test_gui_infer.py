from __future__ import annotations

from ai_arena.gui import _infer_game_spec_from_log
from ai_arena.loading import load_symbol


def test_infer_tictactoe_and_unknown() -> None:
    assert _infer_game_spec_from_log({"game": "tictactoe"}) == "tictactoe"
    assert _infer_game_spec_from_log({"game": "no_such_game"}) is None
    assert _infer_game_spec_from_log({}) is None


def test_infer_reads_name_from_result_game() -> None:
    assert _infer_game_spec_from_log({"result": {"game": "tictactoe"}}) == "tictactoe"


def test_infer_resolves_every_home_game_by_its_real_name() -> None:
    # Regression: the GUI used stale names ("opus_game", "gemini_game"), so
    # Caldera and Photon match logs could not be replayed without --game.
    # Loading each inferred spec and checking .name keeps this in sync with
    # the actual game classes.
    for name in ("skysummit", "caldera", "photon_laser_tactics"):
        spec = _infer_game_spec_from_log({"game": name})
        assert spec is not None, f"no game spec inferred for log name {name!r}"
        game = load_symbol(spec)()
        assert game.name == name

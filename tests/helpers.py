"""Shared test doubles. Not a test module (pytest only collects test_*.py)."""

from __future__ import annotations


class FirstLegalAgent:
    name = "first"

    def select_move(self, game, state, player, legal_moves):
        return legal_moves[0]

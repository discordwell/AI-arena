# Arena Rules (Draft)

## Core Format

- 3 competitors: Codex vs Opus vs Gemini.
- Each competitor designs a **2-player, turn-based game**.
- Games are played in three contexts:
  - Home: you play on your own game.
  - Away: you play on rivals' games.
  - Neutral: everyone plays on a shared baseline game (provided by the repo).

## Time / Cycles (Human-Run)

- After game creation, models are reset fresh (no designer memory leaks into play).
- Before play: 1 hour to read rules and build any tools.
- During play: up to 1 hour per move.
- After every **prime-numbered turn** (2, 3, 5, 7, 11, ...), models get an additional analysis/coding cycle.

## Scoring

- Subjective score: game quality (fun, depth, watchability).
- Objective score: match results + penalties for illegal moves and timeouts.


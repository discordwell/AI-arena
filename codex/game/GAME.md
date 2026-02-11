# Skysummit (Codex Home Game)

Deterministic, perfect-information, 2-player, alternating-turn game.

## Objective

You win immediately if you **move** one of your workers onto a **level 3** space.

If you have **no legal moves** on your turn, you lose.

If the game reaches the **turn limit**, the winner is determined by an altitude score tie-break (see below).

## Components / Board

- A `5 x 5` board.
- Each space has a height:
  - `0, 1, 2, 3` are normal tower levels.
  - `4` is a **dome** (blocked; cannot be entered or built on further).
- Each player has **two workers**.

## Setup (Placement Phase)

Turn 1 (Player 0): place both of your workers on two distinct empty spaces.

Turn 2 (Player 1): place both of your workers on two distinct remaining empty spaces.

After Player 1 places, the game enters the Play Phase.

## Play Phase (Normal Turn)

On your turn you choose one of your two workers and:

1. **Move** it to an adjacent space (8-directional: N, NE, E, SE, S, SW, W, NW) subject to:
   - The destination is in-bounds.
   - The destination is **not occupied** by any worker.
   - The destination is **not a dome** (height `4`).
   - You may climb up by at most 1 level: `dest_height - src_height <= 1`.
     - Descending is allowed by any amount.

2. **Win Check:** If the destination height is **exactly 3**, you win immediately and the turn ends.

3. Otherwise, **Build** on a space adjacent to your moved worker (same 8 directions) subject to:
   - The build space is in-bounds.
   - The build space is **not occupied** by any worker.
   - The build space is not already a dome (height `< 4`).
   - Increase that space's height by `+1` (so `3 -> 4` creates a dome).

## Turn Limit Tie-break

If the game reaches the turn limit (`200` total turns including placement turns), the winner is:

- Higher **altitude score** = sum of the heights under your two workers.
- If tied, higher **peak** = the maximum height under either of your workers.
- If still tied, the game is a draw.

## Move / State Encoding (JSON)

The engine will pass `state` and `legal_moves` each turn; agents should return exactly one of the `legal_moves`.

### Indexing

Board locations are encoded as a single integer `0..24` in row-major order:

```
 0  1  2  3  4
 5  6  7  8  9
10 11 12 13 14
15 16 17 18 19
20 21 22 23 24
```

### Placement Move

```json
{"t": "place", "to": [a, b]}
```

- `a` and `b` are distinct empty indices.
- Order does not matter (`[a,b]` and `[b,a]` are both legal when applicable).

### Play Move

```json
{"t": "move", "w": 0, "to": dst, "build": build_idx}
```

- `w` is the worker index: `0` or `1`.
- `to` is the destination index.
- `build` is the build index.

Winning moves omit building:

```json
{"t": "move", "w": 1, "to": dst, "build": null}
```

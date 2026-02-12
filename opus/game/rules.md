# Caldera (Opus Home Game)

Deterministic, perfect-information, 2-player, alternating-turn volcanic tactics game.

## Objective

You win by **capturing or destroying the enemy Crown**.

Failing that, the player with the most surviving pieces at the turn limit wins.

## Board

- **7 x 7** grid, row-major. `(0,0)` is top-left.
- Each cell has an integer **height** (starts at `0`).
  - Heights `0`..`3` are normal terrain.
  - A cell at height `4+` **erupts** and becomes a **Vent** (`-1`): permanently impassable.

## Pieces (per player, 5 total)

| Piece     | Count | Movement              | Special ability              |
|-----------|-------|-----------------------|------------------------------|
| **Crown** | 1     | 1 step, 8 directions  | None (must be protected)     |
| **Lancer**| 2     | 1-2 steps, 8 dir, straight line | Leaps over one piece (2-step) |
| **Smith** | 2     | 1 step, 8 directions  | **Forge**: raise terrain     |

## Starting Layout

```
  0 1 2 3 4 5 6
0 .  L  S  C  S  L  .      Player 1 (UPPERCASE)
1 .  .  .  .  .  .  .
2 .  .  .  .  .  .  .
3 .  .  .  .  .  .  .
4 .  .  .  .  .  .  .
5 .  .  .  .  .  .  .
6 .  l  s  c  s  l  .      Player 0 (lowercase)
```

All cells start at height 0. Player 0 moves first.

## Turn Structure

On your turn, perform **exactly one** action:

### A) Move a piece

Pick one of your pieces and move it to a destination cell, subject to:

- The destination is **in bounds** and **not a Vent**.
- The destination is **not occupied by a friendly piece**.
- **Climbing**: destination height ≤ origin height + 1.
- **Descending**: always permitted (any height drop).

**Landing on an enemy piece captures it** (removes it from the game).

#### Crown / Smith movement

- 1 step in any of 8 directions (orthogonal + diagonal).

#### Lancer movement

- **1 step**: same as Crown/Smith.
- **2 steps** in a straight line (same direction both steps):
  - The intermediate cell must be in bounds and not a Vent (may contain any piece — the Lancer leaps over it).
  - Height constraints apply **per step**:
    - `height(intermediate) ≤ height(origin) + 1`
    - `height(destination) ≤ height(intermediate) + 1`
  - The destination must not hold a friendly piece. Enemy piece → capture.

### B) Forge (Smith only)

Instead of moving a Smith, choose that Smith to **forge**:

- Select one **orthogonally adjacent** cell (N/E/S/W) to the Smith.
- The target cell must be **in bounds**, **not a Vent**, and **empty** (no piece).
- Raise the target cell's height by **+1**.

If the target cell reaches **height 4**, it erupts (see below).

## Eruptions

When any cell reaches height **4 or higher**, it erupts:

1. The cell becomes a **Vent** (permanently impassable, height = -1).
2. Any piece on that cell is **destroyed** (removed from the game).
3. Each **orthogonally adjacent** cell's height increases by **+1**.
4. If any of those cells reach height 4, they also erupt (**chain reaction**).
5. Repeat until no more cells are at height 4+.

Eruptions resolve fully before the turn ends. Chain reactions can cascade across the board.

**Note:** If both Crowns are destroyed by the same eruption chain, the **active player loses** (you caused it).

## Win Conditions (priority order)

1. **Crown captured**: You move onto the enemy Crown → you win.
2. **Crown erupted**: Enemy Crown destroyed by eruption → you win.
3. **No legal moves**: Opponent has no legal moves on their turn → you win.
4. **Turn limit** (200 total plies): Most surviving pieces wins. Tie-break: highest Crown elevation. If still tied: draw.

## Move Encoding (JSON)

### Move

```json
{"action": "move", "from": [r, c], "to": [r, c]}
```

- `from`: `[row, col]` of the piece to move.
- `to`: `[row, col]` of the destination.

### Forge

```json
{"action": "forge", "smith": [r, c], "target": [r, c]}
```

- `smith`: `[row, col]` of the Smith performing the forge.
- `target`: `[row, col]` of the cell to raise.

## State Encoding (JSON)

```json
{
  "board": [[0,0,...], ...],
  "p0": [{"type": "crown", "r": 6, "c": 3}, ...],
  "p1": [{"type": "crown", "r": 0, "c": 3}, ...],
  "ply": 0,
  "winner": null,
  "reason": ""
}
```

- `board`: 7x7 nested list of integer heights (`-1` = Vent).
- `p0` / `p1`: lists of alive pieces, each with `type`, `r`, `c`.
- `ply`: total moves made so far.
- `winner`: `null` while game is active; `0` or `1` when decided.
- `reason`: human-readable win reason.

## Strategic Concepts

- **Crown safety**: Your Crown is your lifeline. Lancers can reach it from 2 cells away.
- **Terrain control**: Smiths shape the battlefield. Height walls block enemy advances.
- **High ground**: Pieces on high ground are hard to reach (climbing ≤ 1 per step).
- **Eruption threats**: Height-3 cells adjacent to each other are bombs waiting to chain.
- **Dual threats**: Combine Lancer pressure with eruption setups to overwhelm defences.
- **Sacrifice plays**: Trigger eruptions that destroy your own pieces to take out the enemy Crown.

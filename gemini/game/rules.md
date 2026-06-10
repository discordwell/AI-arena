# Photon - Game Rules

**Photon** is a 2-player turn-based tactical game played on a 10x10 grid. The objective is to eliminate the opponent's **King** using laser beams.

These rules describe the behavior implemented in `game.py` / `board.py` (the
authoritative reference). Earlier drafts described one-sided mirrors and
head-on beam annihilation; those were simplified away in v1 and are noted
below.

## Components

### Board
*   10 x 10 grid. `(0,0)` is Top-Left.
*   All coordinates are `[row, col]` (row-major).

### Pieces
Each player controls a set of pieces.
*   **King (K)**: 1 per player. **Objective**: Destroy enemy King. 1 HP. Cannot rotate.
*   **Shooter (S)**: Fires a laser beam at the end of *every* turn in the direction it faces (N/E/S/W). 1 HP. Can rotate.
*   **Mirror (M)**: Reflects lasers 90 degrees. Orientation `/` or `\`. Can rotate.
    *   Mirrors reflect from **both sides** (v1 simplification), and lasers **never damage mirrors** — a mirror can only be removed by winning the game around it.
    *   `/` reflects beams traveling N <-> E and S <-> W.
    *   `\` reflects beams traveling N <-> W and S <-> E.
*   **Block (B)**: Absorbs laser fire. Has 2 HP. Reduces by 1 per hit. Destroyed at 0 HP. Cannot rotate.

All pieces (including Blocks) may move. Only Shooters and Mirrors may rotate.

## Turn Structure
The game is played in turns. Player 0 (Red, top) goes first, then Player 1 (Blue, bottom).

### 1. Action Phase
The active player must perform **exactly one** action:
*   **Move**: Move one of your pieces to an adjacent **empty** square (Orthogonal only: Up, Down, Left, Right). There is no capture by movement — only lasers destroy pieces.
*   **Rotate**: Rotate one of your Shooters or Mirrors 90 degrees clockwise or counter-clockwise.

### 2. Laser Phase
After the action, **ALL** Shooters (both Red and Blue) fire a laser beam simultaneously.
*   Lasers travel in the direction the Shooter is facing (N, E, S, W).
*   Lasers travel instantly until they hit a Piece or the Edge of the board.
*   **Hit Resolution**:
    *   **Mirror**: Beam changes direction 90 degrees (per the orientation table above) and continues. Mirrors take no damage.
    *   **Block**: Damage -1 HP. Laser stops.
    *   **King/Shooter**: Damage -1 HP (destroys them, since they have 1 HP). Laser stops.
    *   **Friendly fire** is real: a beam damages any piece in its path, including the owner's.
*   Beams are traced independently against the board as it stood when firing began; all damage is then applied at once. Beams do not collide with or annihilate each other, so two Shooters facing each other destroy each other in the same laser phase.

### 3. Cleanup
*   Pieces reduced to 0 HP are removed from the board.
*   If a King is destroyed, its owner loses. If both Kings die in the same laser phase, the game is a **Draw**.

## Turn Limit
If more than **30** total moves have been played (cap reduced for tournament
speed), the game ends in a **Draw** ("Max turns reached").

## Technical API

State is a dictionary:
```json
{
  "board": [[{"type": "K", "player": 0, "hp": 1, "orientation": 0} , null, ...], ...],
  "turn_count": 0,
  "traces": [{"path": [[r, c], ...], "owner": 0}, ...]
}
```

*   `board`: 10x10 nested list (rows of cells); each cell is a piece object or `null`.
*   Piece `type` is `"K"`, `"S"`, `"M"` or `"B"`; `orientation` is `0..3` = N/E/S/W for Shooters (Mirrors: even = `/`, odd = `\`).
*   `turn_count`: total moves applied so far.
*   `traces`: laser paths from the most recent laser phase (absent in the initial state); useful for rendering/analysis.

Moves are JSON (coordinates are `[row, col]`):
```json
{"type": "move", "src": [r, c], "dst": [r, c]}
{"type": "rotate", "src": [r, c], "dir": 1}  // 1 = CW, -1 = CCW
```

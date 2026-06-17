# Protocol

This repo is intentionally lightweight. The only hard constraints are:

- 2 players
- alternating turns
- deterministic game rules (given current state and chosen move)

## Python Game Interface

See `src/ai_arena/game.py`.

Required methods:

- `initial_state() -> JSONValue`
- `legal_moves(state, player) -> list[JSONValue]`
- `apply_move(state, player, move) -> JSONValue`
- `terminal(state) -> Terminal`
- `render(state) -> str`

`JSONValue` means the state/move must be JSON-serializable (dict/list/str/int/etc).

## Python Agent Interface

Agents are objects with:

- `name: str`
- `select_move(game, state, player, legal_moves) -> JSONValue`

`select_move` must return one of the supplied `legal_moves` (anything else is a
forfeit). It may raise `TimeoutError` (treated as a timeout) or any other
exception (treated as an agent error) — both forfeit the turn.

### Built-in agents

Selectable by name on the CLI (`--p0`/`--p1`) and in `arena.toml`:

- `human` — reads a move index from stdin.
- `random` — picks a uniformly random legal move.
- `greedy` — a game-agnostic baseline using only this protocol: it takes an
  immediately winning move, otherwise avoids moves that let the opponent win (or
  lose) on the next turn, otherwise plays randomly. Stronger than `random` but
  intentionally shallow (1 ply each way).
- `search` — a game-agnostic depth-limited negamax (alpha-beta) using only this
  protocol, scoring leaves by terminal outcome only (win/loss/draw within the
  search horizon). It plays perfectly on small games (never loses tic-tac-toe)
  and acts like a deeper-horizon `greedy` on larger ones. A per-turn
  `node_budget` bounds its cost on high-branching games.
- `mcts` — a game-agnostic Monte Carlo Tree Search (UCT selection, uniform
  random rollouts, most-visited root move) using only this protocol. Unlike
  `search`, it estimates positions from random playouts instead of a fixed
  horizon, so it is the strongest baseline on the larger arena games — though
  its strength depends on random play reaching decisive endings (weak on games
  that mostly draw under random play). Bounded per turn by `iterations` and a
  `node_budget`.

`random`, `greedy`, `search`, and `mcts` accept a `seed` for reproducible play;
`ai-arena play --seed N` wires it through (each seat gets a distinct derived
seed).

## Subprocess JSONL Agent Protocol

To use a non-Python agent (or to wrap a model harness), run an executable and
exchange one JSON object per line over stdin/stdout.

Engine -> bot:

```json
{
  "type": "turn",
  "game": "tictactoe",
  "player": 0,
  "state": {"board": [0,0,0,0,0,0,0,0,0]},
  "legal_moves": [0,1,2,3,4,5,6,7,8],
  "ts_ms": 1730000000000
}
```

Bot -> engine (success):

```json
{"type": "move", "move": 4}
```

Bot -> engine (failure):

```json
{"type": "error", "error": "api_call_failed: TimeoutError: ..."}
```

Notes:

- Bots may print other JSON message types; the engine ignores unknown `type`s.
- If the bot sends `{"type": "error"}`, the engine treats it as an agent error and the bot forfeits.
- If the bot times out, crashes, or returns an illegal move, it also forfeits.
- Bots should **not** silently fall back to a default move on failure — emit an error instead.


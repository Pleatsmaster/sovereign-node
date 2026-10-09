"""U2A developmental organ: D0-v0, passive.

U2A^cand = U1 + D0. This package contains the frozen D0-v0 organ (d0/),
the candidate-owned vocabulary copy, and the post-terminal hook that
integrates it with exactly one causal edge: mission terminal event ->
D0 recomputation/update.

Public surface:
  hook.on_mission_finished(runner, result)  -- called by MissionRunner.run()
  hook.on_mission_terminal(event, root)     -- processes a terminal event
  hook.materialize_from_log(root)           -- pure recompute f(H, C, V)
  hook.resolve_developmental_root()         -- where developmental state lives

Nothing in this package may: modify worker context, retrieve exemplars into
work, launch a capsule, spend API money, change acceptance, mutate U1, or
admit residues. TRAINING_REQUEST artifacts are data, never commands.
"""

from .hook import (
    materialize_from_log,
    on_mission_finished,
    on_mission_terminal,
    resolve_developmental_root,
)

__all__ = [
    "materialize_from_log",
    "on_mission_finished",
    "on_mission_terminal",
    "resolve_developmental_root",
]

"""LIFE-0: event-driven pulse — the ignition system for Namariel's ordinary life.

v0 scope (boxed): pulse + delta detector + need queue + mission dispatch.
Everything after consequence already largely exists and is NOT built here.

The loop manufactures exposure to reality, not activity:
world delta -> need -> authorized work -> consequence -> history
    -> pressure -> candidate change -> falsification -> inheritance

Non-negotiable v0 properties:
- Cognition is called by difference, not by clock: the pulse makes ZERO
  frontier-model calls and spends nothing. Deterministic sensors only.
- NO_ACTION is a valid life event, recorded every pulse with no delta.
- A need MUST reference an observed delta. No evidence -> no task.
- Dispatch STAGES mission packages. It never launches, never invokes a
  worker, never spends. Launch remains an explicit operator act.
"""
from . import config, delta, dispatch, gate, needs, pulse, sensors

__all__ = ["config", "delta", "dispatch", "gate", "needs", "pulse", "sensors"]

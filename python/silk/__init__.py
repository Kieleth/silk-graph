"""Silk — Distributed knowledge graph engine."""

from silk._native import PyGraphStore as GraphStore
from silk._native import ObservationLog

__all__ = ["GraphStore", "ObservationLog"]
__version__ = "0.1.2"

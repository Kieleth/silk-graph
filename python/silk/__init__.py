"""Silk — Distributed knowledge graph engine."""

from silk._native import PyGraphStore as GraphStore
from silk._native import PyGraphSnapshot as GraphSnapshot
from silk._native import ObservationLog
from silk._native import OperationBuffer
from silk.query import Query, QueryEngine
from silk.compaction import CompactionPolicy, IntervalPolicy, ThresholdPolicy
from silk.views import GraphView

__all__ = [
    "GraphStore", "GraphSnapshot", "ObservationLog", "OperationBuffer",
    "Query", "QueryEngine",
    "CompactionPolicy", "IntervalPolicy", "ThresholdPolicy",
    "GraphView",
]
__version__ = "0.1.6"

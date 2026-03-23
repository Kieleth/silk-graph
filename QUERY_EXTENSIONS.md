# Extending Silk's Query Model

## Why This Matters

Silk's query builder covers ~90% of graph query needs with a Python-native fluent API:

```python
Query(store).nodes("server").where(status="active").follow("RUNS").collect()
```

But some use cases need more:
- **Datalog** — recursive queries, rule-based inference, pattern matching across unbounded paths
- **SPARQL** — RDF compatibility, federated queries across multiple graphs
- **Cypher** — Neo4j-style ASCII art pattern matching
- **Custom DSLs** — domain-specific query languages for your application

Silk doesn't lock you into one query language. The `QueryEngine` protocol lets you plug in any engine without changing Silk's core.

## The QueryEngine Protocol

```python
from silk import QueryEngine

class MyEngine:
    def execute(self, store, query: str) -> list[dict]:
        """Parse query, evaluate against store, return results."""
        ...
```

That's the entire interface. One method. Your engine receives:
- `store` — a `GraphStore` or `GraphSnapshot` with all Silk query methods
- `query` — a string in whatever language your engine understands

It returns a list of result dicts. Silk doesn't care what's inside — your engine defines the schema.

## Example: A Simple Pattern Engine

```python
from silk import Query, QueryEngine

class PatternEngine:
    """Match nodes by type and property patterns."""

    def execute(self, store, query):
        # Parse "type:server status:active" syntax
        filters = {}
        node_type = None
        for part in query.split():
            key, value = part.split(":")
            if key == "type":
                node_type = value
            else:
                filters[key] = value

        q = Query(store).nodes(node_type)
        if filters:
            q = q.where(**filters)
        return q.collect()

# Usage
engine = PatternEngine()
results = Query(store, engine=engine).raw("type:server status:active region:eu-west")
```

## Example: Datalog (Community Contribution)

A Datalog engine would look like:

```python
class DatalogEngine:
    def execute(self, store, query):
        # 1. Parse positive Datalog (facts, variables, conjunction)
        # 2. Project store as EAV facts:
        #    node("srv-1", "server")
        #    property("srv-1", "status", "active")
        #    edge("e1", "RUNS", "srv-1", "svc-api")
        # 3. Bottom-up semi-naive evaluation
        # 4. Return matching variable bindings
        ...

results = Query(store, engine=DatalogEngine()).raw('''
    ?- node(X, "server"),
       edge(_, "RUNS", X, Y),
       property(Y, "status", "down").
''')
# Returns: [{"X": "srv-2", "Y": "svc-db"}]
```

This is ~500-1000 lines of Python. It's a valuable community contribution, not core Silk.

## Why Not Ship Datalog in Core?

1. **Complexity**: A correct Datalog evaluator with semi-naive evaluation, hash joins, and negation is ~1000+ lines of code and a maintenance burden.
2. **Learning curve**: Most Silk users are Python developers. The fluent builder is immediately familiar. Datalog requires learning a new syntax.
3. **Scope**: Silk is a storage + sync engine, not a query engine. Shipping a query language in core would double the API surface.
4. **Choice**: Different applications need different query models. Locking in Datalog excludes SPARQL users (and vice versa).

The foundation (fluent builder + extension protocol) gives you 90% of the power with 10% of the complexity. The remaining 10% is pluggable.

## How to Contribute a Query Engine

1. Implement the `QueryEngine` protocol (one method: `execute`)
2. Publish as a separate package (e.g., `silk-datalog`, `silk-sparql`)
3. Users install your package and pass it to `Query(store, engine=YourEngine())`
4. Submit a PR to add your engine to the "Community Engines" list in this document

## Community Engines

*None yet. Be the first.*

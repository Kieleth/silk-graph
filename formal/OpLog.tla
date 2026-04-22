---- MODULE OpLog ----
\* Silk OpLog — smallest meaningful TLA+ spec.
\*
\* Models a single oplog as a set of entries, each identified by a
\* unique hash with parent links. Verifies:
\*   I-02 (Causal Completeness): every entry's parents exist
\*   I-03 (Append-Only):         entries set never shrinks (temporal)
\*   I-04 (Heads Accuracy):      heads = entries with no successor
\*
\* State:       set of entries (hash + parents) + heads tracking
\* Transitions: append an entry whose parents are all present
\* Invariants:  I-02, I-04 hold after every step; I-03 holds across all pairs

EXTENDS Naturals, FiniteSets

CONSTANTS
    Hashes      \* The universe of possible entry hashes (e.g., {"g", "a", "b", "c", "d"})

VARIABLES
    entries,    \* Set of hashes currently in the oplog
    parents,    \* Function: hash -> set of parent hashes
    heads       \* Set of entries with no successor (the current DAG tips)

vars == <<entries, parents, heads>>

\* --- Type invariant ---
TypeOK ==
    /\ entries \subseteq Hashes
    /\ parents \in [entries -> SUBSET Hashes]
    /\ heads \subseteq entries

\* --- I-02: Causal Completeness ---
\* For every entry in the oplog, all its parents are also in the oplog.
CausalComplete ==
    \A e \in entries : parents[e] \subseteq entries

\* --- I-04: Heads Accuracy ---
\* heads = { e \in entries : no other entry references e as a parent }
\* This is the defining property: an entry is a head iff nothing points to it.
HeadsAccurate ==
    heads = {e \in entries : \A f \in entries : e \notin parents[f]}

\* --- Initial state ---
\* Start with one genesis entry (no parents). Genesis is the only head.
Init ==
    /\ entries = {"g"}
    /\ parents = [h \in {"g"} |-> {}]
    /\ heads   = {"g"}

\* --- Append transition ---
\* Pick a hash not yet in the oplog, pick parents from existing entries, append.
\* Update heads: the new entry becomes a head; its parents are no longer heads.
Append ==
    \E h \in Hashes \ entries :
        \E p \in SUBSET entries :
            /\ entries' = entries \union {h}
            /\ parents' = [x \in entries' |->
                              IF x = h THEN p
                              ELSE parents[x]]
            /\ heads'   = (heads \ p) \union {h}

\* --- Specification ---
Next == Append \/ UNCHANGED vars

Spec == Init /\ [][Next]_vars

\* --- I-03: Append-Only (temporal) ---
\* In every step, the entries set grows or stays the same; it never shrinks.
\* Checked as a TLA+ temporal property via the .cfg file's PROPERTY clause.
AppendOnly == [][entries \subseteq entries']_vars

\* --- Properties to check ---
\* TLC verifies CausalComplete and HeadsAccurate hold in ALL reachable states.
Invariant == CausalComplete /\ HeadsAccurate

====

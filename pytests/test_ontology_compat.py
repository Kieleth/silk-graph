"""Ontology convergence: hashing, fingerprinting, compatibility checks.

Tests the Silk-native ontology identity and compatibility system.
No external dependencies (Malleus, LinkML). Pure Silk ontology struct.
"""

import json
import re

import pytest

from silk import GraphStore


# -- Helpers --

PET_ONTOLOGY = json.dumps({
    "node_types": {
        "animal": {"properties": {"name": {"value_type": "string", "required": True}}},
        "shelter": {"properties": {}},
    },
    "edge_types": {
        "LIVES_AT": {
            "source_types": ["animal"],
            "target_types": ["shelter"],
            "properties": {},
        },
    },
})


def make_store(instance_id: str, ontology: str = PET_ONTOLOGY) -> GraphStore:
    return GraphStore(instance_id, ontology)


# -- content_hash --


class TestOntologyHash:
    def test_hash_is_64_char_hex(self):
        store = make_store("a")
        h = store.ontology_hash()
        assert len(h) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", h)

    def test_hash_deterministic(self):
        a = make_store("inst-a")
        b = make_store("inst-b")
        # Different instance IDs, same ontology → same hash
        assert a.ontology_hash() == b.ontology_hash()

    def test_hash_changes_after_extend(self):
        store = make_store("a")
        hash_before = store.ontology_hash()

        store.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })
        hash_after = store.ontology_hash()
        assert hash_before != hash_after

    def test_hash_same_after_identical_extensions(self):
        """Two stores extended identically → same hash."""
        a = make_store("a")
        b = make_store("b")

        ext = {
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        }
        a.extend_ontology(ext)
        b.extend_ontology(ext)

        assert a.ontology_hash() == b.ontology_hash()


# -- fingerprint --


class TestOntologyFingerprint:
    def test_fingerprint_is_sorted_list(self):
        store = make_store("a")
        fp = store.ontology_fingerprint()
        assert isinstance(fp, list)
        assert fp == sorted(fp)

    def test_fingerprint_contains_types(self):
        store = make_store("a")
        fp = store.ontology_fingerprint()
        assert "type:animal" in fp
        assert "type:shelter" in fp

    def test_fingerprint_contains_edges(self):
        store = make_store("a")
        fp = store.ontology_fingerprint()
        assert "edge:LIVES_AT" in fp
        assert "edge:LIVES_AT:src:animal" in fp
        assert "edge:LIVES_AT:tgt:shelter" in fp

    def test_fingerprint_contains_properties(self):
        store = make_store("a")
        fp = store.ontology_fingerprint()
        assert "prop:animal:name:string:required" in fp

    def test_fingerprint_superset_after_extend(self):
        """Extended ontology's fingerprint is a strict superset."""
        base = make_store("a")
        base_fp = set(base.ontology_fingerprint())

        extended = make_store("b")
        extended.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })
        ext_fp = set(extended.ontology_fingerprint())

        assert base_fp < ext_fp  # strict subset

    def test_fingerprint_with_subtypes(self):
        ont = json.dumps({
            "node_types": {
                "entity": {
                    "properties": {},
                    "subtypes": {
                        "project": {"properties": {"slug": {"value_type": "string", "required": True}}},
                    },
                },
            },
            "edge_types": {},
        })
        store = GraphStore("a", ont)
        fp = store.ontology_fingerprint()
        assert "subtype:entity:project" in fp
        assert "subprop:entity:project:slug:string:required" in fp

    def test_fingerprint_with_parent_type(self):
        ont = json.dumps({
            "node_types": {
                "entity": {"properties": {}},
                "server": {"properties": {}, "parent_type": "entity"},
            },
            "edge_types": {},
        })
        store = GraphStore("a", ont)
        fp = store.ontology_fingerprint()
        assert "type:server:parent:entity" in fp

    def test_fingerprint_with_enum_constraints(self):
        ont = json.dumps({
            "node_types": {
                "server": {
                    "properties": {
                        "status": {
                            "value_type": "string",
                            "required": True,
                            "constraints": {"enum": ["active", "standby"]},
                        },
                    },
                },
            },
            "edge_types": {},
        })
        store = GraphStore("a", ont)
        fp = store.ontology_fingerprint()
        assert "constraint:server:status:enum:active" in fp
        assert "constraint:server:status:enum:standby" in fp


# -- check_compatibility --


class TestOntologyCompatibility:
    def test_identical(self):
        a = make_store("a")
        b = make_store("b")
        verdict = a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()
        )
        assert verdict == "identical"

    def test_superset(self):
        """Local has more types than remote → superset."""
        base = make_store("base")
        extended = make_store("ext")
        extended.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })

        verdict = extended.check_ontology_compatibility(
            base.ontology_hash(), base.ontology_fingerprint()
        )
        assert verdict == "superset"

    def test_subset(self):
        """Local has fewer types than remote → subset."""
        base = make_store("base")
        extended = make_store("ext")
        extended.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })

        verdict = base.check_ontology_compatibility(
            extended.ontology_hash(), extended.ontology_fingerprint()
        )
        assert verdict == "subset"

    def test_divergent(self):
        """Two independent extensions → divergent."""
        branch_a = make_store("a")
        branch_a.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })

        branch_b = make_store("b")
        branch_b.extend_ontology({
            "node_types": {"adoption": {"properties": {}}},
            "edge_types": {},
        })

        verdict = branch_a.check_ontology_compatibility(
            branch_b.ontology_hash(), branch_b.ontology_fingerprint()
        )
        assert verdict == "divergent"

    def test_compatible_after_sync_extend(self):
        """After syncing an ExtendOntology, stores become identical."""
        a = make_store("a")
        b = make_store("b")

        # B extends
        b.extend_ontology({
            "node_types": {"volunteer": {"properties": {}}},
            "edge_types": {},
        })

        # Before sync: A is subset
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()
        ) == "subset"

        # Sync B → A (the ExtendOntology entry transfers)
        offer_a = a.generate_sync_offer()
        payload = b.receive_sync_offer(offer_a)
        a.merge_sync_payload(payload)

        # After sync: identical
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()
        ) == "identical"

    def test_pet_shelter_scenario(self):
        """The FAQ example: pet shelter with ontology drift.

        Peer A: animal, shelter. Peer B: same + volunteer + microchip_id.
        A is subset of B. After sync, A evolves to match B.
        """
        a = make_store("shelter-a")
        b = make_store("shelter-b")

        # B extends: adds volunteer type and microchip_id property on animal
        b.extend_ontology({
            "node_types": {"volunteer": {"properties": {"name": {"value_type": "string"}}}},
            "edge_types": {},
            "node_type_updates": {
                "animal": {
                    "add_properties": {"microchip_id": {"value_type": "string"}},
                },
            },
        })

        # B creates data using the new types
        b.add_node("max", "animal", "Max", {"name": "Max", "microchip_id": "UK-123"})
        b.add_node("alice", "volunteer", "Alice", {"name": "Alice"})

        # A doesn't know about volunteer
        fp_a = set(a.ontology_fingerprint())
        fp_b = set(b.ontology_fingerprint())
        assert "type:volunteer" not in fp_a
        assert "type:volunteer" in fp_b
        assert fp_a < fp_b  # strict subset

        verdict = a.check_ontology_compatibility(b.ontology_hash(), b.ontology_fingerprint())
        assert verdict == "subset"

        # Sync B → A: ExtendOntology + data entries transfer
        offer_a = a.generate_sync_offer()
        payload = b.receive_sync_offer(offer_a)
        a.merge_sync_payload(payload)

        # A now has the extended ontology AND the data
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()
        ) == "identical"
        assert a.get_node("max") is not None
        assert a.get_node("max")["properties"]["microchip_id"] == "UK-123"
        assert a.get_node("alice") is not None


# -- Inquisition H3/S10/S3: the fingerprint must see everything the validator enforces --


# The single source of truth for enforced constraint names. The Rust validator
# exposes the same list; the test below asserts they agree, so adding a ninth
# constraint without fingerprinting it fails the build.
ENFORCED_CONSTRAINTS = {
    "enum": (["a", "b"], "c"),
    "min": (1, 0),
    "max": (8, 50),
    "min_exclusive": (1, 1),
    "max_exclusive": (8, 8),
    "min_length": (3, "ab"),
    "max_length": (3, "abcd"),
    "pattern": ("^a+$", "b"),
}


def _ont_with_constraint(name, value, value_type):
    return json.dumps({
        "node_types": {
            "s": {"properties": {"p": {"value_type": value_type,
                                       "constraints": {name: value}}}}
        },
        "edge_types": {},
    })


def _value_type_for(name):
    return "string" if name in {"enum", "min_length", "max_length", "pattern"} else "int"


class TestFingerprintCoversValidator:
    def test_enforced_constraint_names_match_validator(self):
        """S3/H3: the known-constraint list lives in one place. If the Rust
        validator learns a ninth constraint, this fails until it is listed."""
        from silk import enforced_constraint_names

        assert set(enforced_constraint_names()) == set(ENFORCED_CONSTRAINTS)

    @pytest.mark.parametrize("name", sorted(ENFORCED_CONSTRAINTS))
    def test_constraint_divergence_is_visible_in_fingerprint(self, name):
        """H3: two ontologies differing only in one enforced constraint must
        produce different fingerprints and a divergent verdict. Before the fix
        only 'enum' emitted a fact, so seven of eight read 'identical'."""
        vt = _value_type_for(name)
        constrained, _ = ENFORCED_CONSTRAINTS[name]
        a = make_store("a", _ont_with_constraint(name, constrained, vt))
        # Same slot, no constraint at all — strictly fewer facts.
        b = make_store("b", json.dumps({
            "node_types": {"s": {"properties": {"p": {"value_type": vt}}}},
            "edge_types": {},
        }))

        assert a.ontology_fingerprint() != b.ontology_fingerprint()
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) != "identical"

    @pytest.mark.parametrize("name", sorted(ENFORCED_CONSTRAINTS))
    def test_differing_constraint_values_are_divergent(self, name):
        """H3: same constraint, different bound — the federation case. Two
        teams extending the shared root with the same slot and different
        limits must not read 'identical'.

        `enum` is deliberately the exception: its members are emitted as
        individual facts, so a superset of allowed values is a genuine
        superset (a relaxation), not a fork. Disjoint member sets are the
        divergent case for enum, and that is what is asserted here.
        """
        vt = _value_type_for(name)
        tight, _ = ENFORCED_CONSTRAINTS[name]
        loose = {"enum": ["y", "z"], "min": 0, "max": 100,
                 "min_exclusive": 0, "max_exclusive": 100,
                 "min_length": 1, "max_length": 99, "pattern": "^z+$"}[name]
        a = make_store("a", _ont_with_constraint(name, tight, vt))
        b = make_store("b", _ont_with_constraint(name, loose, vt))

        assert a.ontology_hash() != b.ontology_hash()
        assert a.ontology_fingerprint() != b.ontology_fingerprint()
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) == "divergent"

    def test_widening_an_enum_is_a_superset_not_a_fork(self):
        """H3: adding an allowed value is monotonic relaxation."""
        a = make_store("a", _ont_with_constraint("enum", ["a", "b", "c"], "string"))
        b = make_store("b", _ont_with_constraint("enum", ["a", "b"], "string"))
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) == "superset"

    def test_edge_type_properties_produce_facts(self):
        """H3: EdgeTypeDef.properties emitted no facts at all."""
        mk = lambda c: json.dumps({
            "node_types": {"a": {"properties": {}}},
            "edge_types": {"R": {"source_types": ["a"], "target_types": ["a"],
                                 "properties": {"w": {"value_type": "int",
                                                      "constraints": c}}}},
        })
        a = make_store("a", mk({"max": 100}))
        b = make_store("b", mk({"max": 8}))

        assert a.ontology_hash() != b.ontology_hash()
        assert a.ontology_fingerprint() != b.ontology_fingerprint()
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) == "divergent"

    def test_edge_property_presence_is_a_fact(self):
        """H3: an edge property that exists on one side only must be visible."""
        with_prop = json.dumps({
            "node_types": {"a": {"properties": {}}},
            "edge_types": {"R": {"source_types": ["a"], "target_types": ["a"],
                                 "properties": {"w": {"value_type": "int"}}}},
        })
        without = json.dumps({
            "node_types": {"a": {"properties": {}}},
            "edge_types": {"R": {"source_types": ["a"], "target_types": ["a"],
                                 "properties": {}}},
        })
        a = make_store("a", with_prop)
        b = make_store("b", without)
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) == "superset"

    def test_federation_path_divergence_is_reported(self):
        """H3: reachable from the ordinary monotonic path — same genesis, two
        peers each extending with the same slot and different bounds."""
        base = json.dumps({"node_types": {"s": {"properties": {}}}, "edge_types": {}})
        a = make_store("a", base)
        b = make_store("b", base)
        a.extend_ontology({"node_type_updates": {"s": {"add_properties": {
            "cpu": {"value_type": "int", "constraints": {"max": 100}}}}}})
        b.extend_ontology({"node_type_updates": {"s": {"add_properties": {
            "cpu": {"value_type": "int", "constraints": {"max": 8}}}}}})

        assert a.ontology_hash() != b.ontology_hash()
        assert a.check_ontology_compatibility(
            b.ontology_hash(), b.ontology_fingerprint()) == "divergent"

    def test_inherited_property_is_a_membership_fact(self):
        """H3: emit membership from the resolved table, so a slot attached via
        parent_type is a fact on the child too."""
        ont = json.dumps({
            "node_types": {
                "base": {"properties": {"tag": {"value_type": "string"}}},
                "child": {"properties": {}, "parent_type": "base"},
            },
            "edge_types": {},
        })
        fp = make_store("a", ont).ontology_fingerprint()
        assert any(f.startswith("prop:child:tag:") for f in fp), (
            f"inherited slot not present as a fact on the child: {fp}")


class TestFingerprintVersion:
    def test_version_fact_is_present(self):
        """S10: without a version fact, fixing H3 reads as a fork against every
        old peer and operators learn to ignore the signal."""
        fp = make_store("a").ontology_fingerprint()
        assert any(f.startswith("fingerprint_version:") for f in fp)

    def test_version_mismatch_is_not_a_false_superset(self):
        """S10: an emitter upgrade must be distinguishable from a real fork.

        A peer running the old formula emits fewer facts for the same
        ontology. Without a version fact that reads as a clean 'superset' —
        indistinguishable from a peer that genuinely has less schema. With
        it, the two fingerprints disagree on a fact that is about the
        emitter, so the verdict is 'divergent' and an operator can see why.
        """
        store = make_store("a", _ont_with_constraint("max", 8, "int"))
        old_emitter = [f for f in store.ontology_fingerprint()
                       if not f.startswith(("fingerprint_version:", "constraint:"))]
        old_emitter.append("fingerprint_version:1")

        # Differing hash: the peers are being compared for real, not
        # short-circuited by the identical-hash fast path.
        other_hash = "0" * 64
        assert store.check_ontology_compatibility(other_hash, old_emitter) == "divergent"


class TestUnknownConstraintNames:
    def test_typo_constraint_is_rejected_at_construction(self):
        """S3: 'maximum' instead of 'max' was accepted, never enforced, and
        invisible to the fingerprint. Silence must be a choice, not a typo."""
        with pytest.raises(ValueError) as exc:
            make_store("a", _ont_with_constraint("maximum", 8, "int"))
        assert "maximum" in str(exc.value)

    def test_typo_constraint_is_rejected_on_extension(self):
        """S3: the same gate on the other ontology entry point."""
        store = make_store("a", json.dumps(
            {"node_types": {"s": {"properties": {}}}, "edge_types": {}}))
        with pytest.raises(ValueError) as exc:
            store.extend_ontology({"node_type_updates": {"s": {"add_properties": {
                "cpu": {"value_type": "int", "constraints": {"maximum": 8}}}}}})
        assert "maximum" in str(exc.value)

    def test_x_prefixed_constraints_are_allowed(self):
        """S3: forward compatibility stays available, but explicit."""
        store = make_store("a", _ont_with_constraint("x_community_rule", 8, "int"))
        assert store is not None

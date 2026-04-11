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

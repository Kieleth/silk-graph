"""Extensions that would delete existing data.

R-03 promises additive-only ontology evolution. The check used to be purely
syntactic: it asked whether the new ontology's *declarations* were a superset
of the old one's, never whether entries already in the oplog survive. Five
shapes passed that check and retroactively invalidated data. Four of them are
detectable from the extension alone and are now refused (A). The fifth depends
on what open properties existing nodes happen to carry, so it cannot be
refused syntactically; `preview_extension` is the way to see it (C).

Reported by a downstream consumer after an outage (2026-08-31), where the
subtype flip was cleared as additive by two reviewers and by silk's own
compatibility check.
"""

import json
import os
import tempfile

import pytest

from silk import GraphStore

ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {"properties": {"name": {"value_type": "string", "required": True}}}
    },
    "edge_types": {
        "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
    },
})

SUBTYPED_ONTOLOGY = json.dumps({
    "node_types": {
        "entity": {
            "properties": {"name": {"value_type": "string", "required": True}},
            "subtypes": {"switch": {"properties": {}}},
        }
    },
    "edge_types": {},
})

ADD_SUBTYPES = {
    "node_type_updates": {"entity": {"add_subtypes": {"router": {"properties": {}}}}}
}
ADD_REQUIRED_NODE_PROP = {
    "node_type_updates": {
        "entity": {"add_properties": {"owner": {"value_type": "string", "required": True}}}
    }
}
ADD_REQUIRED_EDGE_PROP = {
    "edge_type_updates": {
        "LINKS": {"add_properties": {"weight": {"value_type": "int", "required": True}}}
    }
}
ADD_SUBTYPE_WITH_REQUIRED_PROP = {
    "node_type_updates": {
        "entity": {
            "add_subtypes": {
                "router": {"properties": {"ip": {"value_type": "string", "required": True}}}
            }
        }
    }
}
ADD_OPTIONAL_NODE_PROP = {
    "node_type_updates": {
        "entity": {"add_properties": {"tag": {"value_type": "string", "required": False}}}
    }
}


def _populated(tmpdir, name="s.redb", ontology=ONTOLOGY):
    store = GraphStore("inst-1", ontology, os.path.join(tmpdir, name))
    store.add_node("a", "entity", "A", {"name": "a"})
    store.add_node("b", "entity", "B", {"name": "b"})
    if "LINKS" in ontology:
        store.add_edge("e1", "LINKS", "a", "b", {})
    return store


# -- A: the four detectable shapes are refused --


@pytest.mark.parametrize("extension,fragment", [
    (ADD_SUBTYPES, "subtype"),
    (ADD_REQUIRED_NODE_PROP, "owner"),
    (ADD_REQUIRED_EDGE_PROP, "weight"),
])
def test_destructive_extension_is_refused(extension, fragment):
    """The extension is rejected and the store is untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        with pytest.raises(ValueError) as exc:
            store.extend_ontology(json.dumps(extension))
        assert fragment in str(exc.value)

        assert store.get_node("a") is not None
        assert store.get_edge("e1") is not None
        assert store.get_quarantined_details() == []


def test_required_property_on_a_new_subtype_is_refused():
    """A node may already carry an undeclared subtype name (D-026 accepts it,
    validating type-level properties only). Declaring that subtype with a
    required property would quarantine it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = GraphStore("i", SUBTYPED_ONTOLOGY, os.path.join(tmpdir, "s.redb"))
        store.add_node("n1", "entity", "N", {"name": "a"}, subtype="router")

        with pytest.raises(ValueError) as exc:
            store.extend_ontology(json.dumps(ADD_SUBTYPE_WITH_REQUIRED_PROP))
        assert "ip" in str(exc.value)
        assert store.get_node("n1") is not None


def test_refusal_names_the_consequence():
    """The message has to say what would happen, not just 'rejected'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        with pytest.raises(ValueError) as exc:
            store.extend_ontology(json.dumps(ADD_REQUIRED_NODE_PROP))
        msg = str(exc.value)
        assert "quarantine" in msg.lower()
        assert "required: false" in msg or "optional" in msg


# -- A: the controls. These stay legal. --


def test_optional_property_is_still_additive():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        store.extend_ontology(json.dumps(ADD_OPTIONAL_NODE_PROP))
        assert store.get_node("a") is not None
        assert store.get_edge("e1") is not None
        assert store.get_quarantined_details() == []


def test_subtype_without_required_properties_is_still_additive():
    """Adding a subtype to an already-subtyped type, carrying no required
    properties. This is the shape a downstream migration used."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = GraphStore("i", SUBTYPED_ONTOLOGY, os.path.join(tmpdir, "s.redb"))
        store.add_node("n1", "entity", "N", {"name": "a"}, subtype="switch")
        store.extend_ontology(json.dumps(ADD_SUBTYPES))
        assert store.get_node("n1") is not None


def test_required_property_on_a_brand_new_type_is_allowed():
    """No data can exist for a type that does not exist yet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        store.extend_ontology(json.dumps({
            "node_types": {
                "service": {"properties": {"url": {"value_type": "string", "required": True}}}
            }
        }))
        store.add_node("s1", "service", "S", {"url": "http://x"})
        assert store.get_node("s1") is not None


def test_widening_edge_endpoints_is_still_additive():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        store.extend_ontology(json.dumps({
            "node_types": {"service": {"properties": {}}},
            "edge_type_updates": {"LINKS": {"add_source_types": ["service"]}},
        }))
        assert store.get_edge("e1") is not None


# -- The shape that cannot be refused syntactically --


def test_declaring_an_optional_property_can_still_invalidate_open_property_nodes():
    """A node written with an undeclared extra property is accepted. Declaring
    that property later, even as optional, type-checks it retroactively.

    Whether this destroys data depends on values in the oplog, not on the
    extension, so it stays legal. preview_extension is how you see it coming.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store = GraphStore("i", ONTOLOGY, os.path.join(tmpdir, "o.redb"))
        store.add_node("n1", "entity", "N", {"name": "a", "port": "not-an-int"})
        assert store.get_node("n1") is not None

        ext = {"node_type_updates": {
            "entity": {"add_properties": {"port": {"value_type": "int", "required": False}}}
        }}
        assert store.preview_extension(json.dumps(ext))["would_quarantine"] == 1

        store.extend_ontology(json.dumps(ext))
        assert store.get_node("n1") is None


# -- C: preview_extension --


def test_preview_reports_zero_for_a_safe_extension():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        result = store.preview_extension(json.dumps(ADD_OPTIONAL_NODE_PROP))
        assert result["would_quarantine"] == 0
        assert result["would_rescue"] == 0
        assert result["samples"] == []


def test_preview_does_not_mutate_the_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        before_len, before_hash = store.len(), store.ontology_hash()

        ext = {"node_type_updates": {
            "entity": {"add_properties": {"port": {"value_type": "int", "required": False}}}
        }}
        store.preview_extension(json.dumps(ext))

        assert store.len() == before_len
        assert store.ontology_hash() == before_hash
        assert store.get_node("a") is not None


def test_preview_samples_name_the_entry_and_the_reason():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = GraphStore("i", ONTOLOGY, os.path.join(tmpdir, "o.redb"))
        store.add_node("n1", "entity", "N", {"name": "a", "port": "not-an-int"})

        result = store.preview_extension(json.dumps({"node_type_updates": {
            "entity": {"add_properties": {"port": {"value_type": "int", "required": False}}}
        }}))
        assert result["would_quarantine"] == 1
        sample = result["samples"][0]
        assert sample["op"] == "add_node"
        assert "port" in sample["reason"]
        assert len(sample["hash"]) == 64


def test_preview_reports_entries_that_would_be_rescued():
    """An entry quarantined for an unknown node type becomes valid once the
    extension declares it. Preview counts that too, so the answer to "should I
    run this" is the whole ledger and not only the losses."""
    wider = json.dumps({
        "node_types": {
            "entity": {"properties": {"name": {"value_type": "string", "required": True}}},
            "service": {"properties": {}},
        },
        "edge_types": {
            "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
        },
    })
    with tempfile.TemporaryDirectory() as tmpdir:
        a = GraphStore("a", wider, os.path.join(tmpdir, "a.redb"))
        a.add_node("s1", "service", "S", {})

        b = GraphStore("b", ONTOLOGY, os.path.join(tmpdir, "b.redb"))
        for _ in range(2):
            b.merge_sync_payload(a.receive_sync_offer(b.generate_sync_offer()))
        assert b.get_node("s1") is None
        assert len(b.get_quarantined()) == 1

        result = b.preview_extension(json.dumps({
            "node_types": {"service": {"properties": {}}}
        }))
        assert result["would_rescue"] == 1
        assert result["would_quarantine"] == 0


def test_preview_rejects_an_extension_that_extend_would_refuse():
    """Preview answers the same question extend_ontology does, so it must not
    report '0 lost' for something that will be refused outright."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _populated(tmpdir)
        with pytest.raises(ValueError):
            store.preview_extension(json.dumps(ADD_REQUIRED_NODE_PROP))


# -- D: the compatibility verdict --

# A peer can hold any of these as its *genesis* ontology, so they reach us over
# sync regardless of what merge_extension refuses locally.
SUBTYPED_GENESIS = json.dumps({
    "node_types": {
        "entity": {
            "properties": {"name": {"value_type": "string", "required": True}},
            "subtypes": {"router": {"properties": {}}},
        }
    },
    "edge_types": {
        "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
    },
})
REQUIRED_PROP_GENESIS = json.dumps({
    "node_types": {
        "entity": {"properties": {
            "name": {"value_type": "string", "required": True},
            "owner": {"value_type": "string", "required": True},
        }}
    },
    "edge_types": {
        "LINKS": {"source_types": ["entity"], "target_types": ["entity"], "properties": {}}
    },
})
REQUIRED_EDGE_PROP_GENESIS = json.dumps({
    "node_types": {
        "entity": {"properties": {"name": {"value_type": "string", "required": True}}}
    },
    "edge_types": {
        "LINKS": {"source_types": ["entity"], "target_types": ["entity"],
                  "properties": {"weight": {"value_type": "int", "required": True}}}
    },
})


@pytest.mark.parametrize("genesis", [
    SUBTYPED_GENESIS,
    REQUIRED_PROP_GENESIS,
    REQUIRED_EDGE_PROP_GENESIS,
])
def test_compat_reports_destructive_superset(genesis):
    """A peer running an older silk can still hand us one of these, and it can
    always be someone's genesis. The verdict has to separate it from a safe
    superset, and an old consumer matching only on "superset" must fall through
    to its refuse branch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old = GraphStore("old", ONTOLOGY, os.path.join(tmpdir, "old.redb"))
        old_hash, old_fp = old.ontology_hash(), old.ontology_fingerprint()

        new = GraphStore("new", genesis, os.path.join(tmpdir, "new.redb"))

        assert new.check_ontology_compatibility(old_hash, old_fp) == "destructive_superset"


def test_compat_still_reports_plain_superset_for_a_safe_extension():
    with tempfile.TemporaryDirectory() as tmpdir:
        old = GraphStore("old", ONTOLOGY, os.path.join(tmpdir, "old.redb"))
        old_hash, old_fp = old.ontology_hash(), old.ontology_fingerprint()

        new = GraphStore("new", ONTOLOGY, os.path.join(tmpdir, "new.redb"))
        new.extend_ontology(json.dumps(ADD_OPTIONAL_NODE_PROP))

        assert new.check_ontology_compatibility(old_hash, old_fp) == "superset"


def test_compat_identical_and_subset_are_unchanged():
    """The new arm must not swallow the other verdicts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old = GraphStore("old", ONTOLOGY, os.path.join(tmpdir, "old.redb"))
        old_hash, old_fp = old.ontology_hash(), old.ontology_fingerprint()

        same = GraphStore("same", ONTOLOGY, os.path.join(tmpdir, "same.redb"))
        assert same.check_ontology_compatibility(old_hash, old_fp) == "identical"

        newer = GraphStore("newer", ONTOLOGY, os.path.join(tmpdir, "newer.redb"))
        newer.extend_ontology(json.dumps(ADD_OPTIONAL_NODE_PROP))
        assert old.check_ontology_compatibility(
            newer.ontology_hash(), newer.ontology_fingerprint()) == "subset"


def test_destructive_changes_lists_what_would_break():
    with tempfile.TemporaryDirectory() as tmpdir:
        old = GraphStore("old", ONTOLOGY, os.path.join(tmpdir, "old.redb"))
        old_fp = old.ontology_fingerprint()

        new = GraphStore("new", REQUIRED_PROP_GENESIS, os.path.join(tmpdir, "new.redb"))

        changes = new.ontology_destructive_changes(old_fp)
        assert len(changes) == 1
        assert "owner" in changes[0]
        assert "entity" in changes[0]


def test_destructive_changes_is_empty_for_a_safe_extension():
    with tempfile.TemporaryDirectory() as tmpdir:
        old = GraphStore("old", ONTOLOGY, os.path.join(tmpdir, "old.redb"))
        old_fp = old.ontology_fingerprint()

        new = GraphStore("new", ONTOLOGY, os.path.join(tmpdir, "new.redb"))
        new.extend_ontology(json.dumps(ADD_OPTIONAL_NODE_PROP))

        assert new.ontology_destructive_changes(old_fp) == []

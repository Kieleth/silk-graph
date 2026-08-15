use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

use crate::clock::LamportClock;
use crate::ontology::{Ontology, OntologyExtension};

/// Property value — supports the types needed for graph node/edge properties.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
    List(Vec<Value>),
    Map(BTreeMap<String, Value>),
}

/// Graph operations — the payload of each Merkle-DAG entry.
///
/// `DefineOntology` must be the first (genesis) entry. All subsequent
/// operations are validated against the ontology it defines.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op")]
pub enum GraphOp {
    /// Genesis entry — defines the initial ontology (extendable via R-03 ExtendOntology).
    /// Must be the first entry in the DAG (next = []).
    #[serde(rename = "define_ontology")]
    DefineOntology { ontology: Ontology },
    #[serde(rename = "add_node")]
    AddNode {
        node_id: String,
        node_type: String,
        #[serde(default)]
        subtype: Option<String>,
        label: String,
        #[serde(default)]
        properties: BTreeMap<String, Value>,
    },
    #[serde(rename = "add_edge")]
    AddEdge {
        edge_id: String,
        edge_type: String,
        source_id: String,
        target_id: String,
        #[serde(default)]
        properties: BTreeMap<String, Value>,
    },
    #[serde(rename = "update_property")]
    UpdateProperty {
        entity_id: String,
        key: String,
        value: Value,
    },
    #[serde(rename = "remove_node")]
    RemoveNode { node_id: String },
    #[serde(rename = "remove_edge")]
    RemoveEdge { edge_id: String },
    /// R-03: Extend the ontology with new types/properties (monotonic only).
    #[serde(rename = "extend_ontology")]
    ExtendOntology { extension: OntologyExtension },
    /// Reserved: schema transform lenses for cross-ontology projection.
    /// Opaque transforms field — interpretation is application-defined.
    /// Reserved now to avoid future wire format break.
    #[serde(rename = "define_lens")]
    DefineLens { transforms: Vec<u8> },
    /// R-08: Checkpoint entry — summarizes all prior state.
    /// Contains synthetic ops that reconstruct the full graph when replayed.
    /// After compaction, this becomes the new genesis (next=[]).
    #[serde(rename = "checkpoint")]
    Checkpoint {
        /// Synthetic ops that reconstruct the graph state
        ops: Vec<GraphOp>,
        /// Per-op clocks: (physical_ms, logical) for each op.
        /// Bug 6 fix: preserves per-entity clock metadata for correct LWW after compaction.
        #[serde(default)]
        op_clocks: Vec<(u64, u32)>,
        /// Physical timestamp when compaction was performed
        compacted_at_physical_ms: u64,
        /// Logical timestamp when compaction was performed
        compacted_at_logical: u32,
    },
}

/// A 32-byte BLAKE3 hash, used as the content address for entries.
pub type Hash = [u8; 32];

/// A single entry in the Merkle-DAG operation log.
///
/// Each entry is content-addressed: `hash = BLAKE3(msgpack(signable_content))`.
/// The hash covers the payload, causal links, and clock — NOT the hash itself.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Entry {
    /// BLAKE3 hash of the signable content (payload + next + refs + clock + author)
    pub hash: Hash,
    /// The graph mutation (or genesis ontology definition)
    pub payload: GraphOp,
    /// Causal predecessors — hashes of the DAG heads at time of write
    pub next: Vec<Hash>,
    /// Reserved. Currently unused (always empty). Part of the hash computation
    /// for wire format stability. May be used for skip-list traversal in future versions.
    #[serde(default)]
    pub refs: Vec<Hash>,
    /// Lamport clock at time of creation
    pub clock: LamportClock,
    /// Author instance identifier
    pub author: String,
    /// D-027: ed25519 signature over the hash bytes (64 bytes). None for unsigned (pre-v0.3) entries.
    #[serde(default)]
    pub signature: Option<Vec<u8>>,
}

/// Entries serialize as a POSITIONAL msgpack array — no field names on the
/// wire or on disk — so the field count is part of the format.
///
/// S4 removed a ninth-hour `ontology_hash` field that was never populated in
/// production. Deserialization therefore accepts both shapes: the current
/// 7-element form and the legacy 8-element form, whose trailing element is
/// consumed and discarded. Consuming it matters: entries are read as a
/// `Vec<Entry>`, so leaving a stray element in the stream would corrupt every
/// entry after it.
///
/// This is one-directional. A build with this shim reads stores and peers
/// written by older builds; an older build cannot read the 7-element entries
/// this one writes.
impl<'de> Deserialize<'de> for Entry {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct EntryVisitor;

        impl<'de> serde::de::Visitor<'de> for EntryVisitor {
            type Value = Entry;

            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("an Entry as a 7-element sequence (or legacy 8-element)")
            }

            fn visit_seq<A>(self, mut seq: A) -> Result<Entry, A::Error>
            where
                A: serde::de::SeqAccess<'de>,
            {
                use serde::de::Error as _;
                let hash = seq
                    .next_element()?
                    .ok_or_else(|| A::Error::invalid_length(0, &"8 fields"))?;
                let payload = seq
                    .next_element()?
                    .ok_or_else(|| A::Error::invalid_length(1, &"8 fields"))?;
                let next = seq
                    .next_element()?
                    .ok_or_else(|| A::Error::invalid_length(2, &"8 fields"))?;
                let refs = seq.next_element()?.unwrap_or_default();
                let clock = seq
                    .next_element()?
                    .ok_or_else(|| A::Error::invalid_length(4, &"8 fields"))?;
                let author = seq
                    .next_element()?
                    .ok_or_else(|| A::Error::invalid_length(5, &"8 fields"))?;
                let signature = seq.next_element()?.unwrap_or(None);
                // Legacy trailing `ontology_hash`. Always None in production;
                // consumed so the surrounding stream stays aligned.
                let _legacy: Option<Option<Hash>> = seq.next_element()?;

                Ok(Entry {
                    hash,
                    payload,
                    next,
                    refs,
                    clock,
                    author,
                    signature,
                })
            }
        }

        deserializer.deserialize_seq(EntryVisitor)
    }
}

/// The portion of an Entry that gets hashed. Signature is NOT included
/// (the signature covers the hash, not vice versa).
#[derive(Serialize)]
struct SignableContent<'a> {
    payload: &'a GraphOp,
    next: &'a Vec<Hash>,
    refs: &'a Vec<Hash>,
    clock: &'a LamportClock,
    author: &'a str,
}

impl Entry {
    /// Create a new unsigned entry with computed BLAKE3 hash.
    pub fn new(
        payload: GraphOp,
        next: Vec<Hash>,
        refs: Vec<Hash>,
        clock: LamportClock,
        author: impl Into<String>,
    ) -> Self {
        let author = author.into();
        let hash = Self::compute_hash(&payload, &next, &refs, &clock, &author);
        Self {
            hash,
            payload,
            next,
            refs,
            clock,
            author,
            signature: None,
        }
    }

    /// D-027: Create a new signed entry. Computes hash, then signs it with ed25519.
    #[cfg(feature = "signing")]
    pub fn new_signed(
        payload: GraphOp,
        next: Vec<Hash>,
        refs: Vec<Hash>,
        clock: LamportClock,
        author: impl Into<String>,
        signing_key: &ed25519_dalek::SigningKey,
    ) -> Self {
        use ed25519_dalek::Signer;
        let author = author.into();
        let hash = Self::compute_hash(&payload, &next, &refs, &clock, &author);
        let sig = signing_key.sign(&hash);
        Self {
            hash,
            payload,
            next,
            refs,
            clock,
            author,
            signature: Some(sig.to_bytes().to_vec()),
        }
    }

    /// D-027: Verify the ed25519 signature on this entry against a public key.
    /// Returns true if signature is valid, false if invalid.
    /// Returns true if no signature is present (unsigned entry — backward compatible).
    #[cfg(feature = "signing")]
    pub fn verify_signature(&self, public_key: &ed25519_dalek::VerifyingKey) -> bool {
        use ed25519_dalek::Verifier;
        match &self.signature {
            Some(sig_bytes) => {
                if sig_bytes.len() != 64 {
                    return false;
                }
                let mut sig_array = [0u8; 64];
                sig_array.copy_from_slice(sig_bytes);
                let sig = ed25519_dalek::Signature::from_bytes(&sig_array);
                public_key.verify(&self.hash, &sig).is_ok()
            }
            None => true, // unsigned entries accepted (migration mode)
        }
    }

    /// Check whether this entry has a signature.
    pub fn is_signed(&self) -> bool {
        self.signature.is_some()
    }

    /// Compute the BLAKE3 hash of the signable content.
    fn compute_hash(
        payload: &GraphOp,
        next: &Vec<Hash>,
        refs: &Vec<Hash>,
        clock: &LamportClock,
        author: &str,
    ) -> Hash {
        let signable = SignableContent {
            payload,
            next,
            refs,
            clock,
            author,
        };
        // Safety: rmp_serde serialization of #[derive(Serialize)] structs with known
        // types (String, i64, bool, Vec, BTreeMap) cannot fail. Same pattern as sled/redb.
        let bytes = rmp_serde::to_vec(&signable).expect("serialization should not fail");
        *blake3::hash(&bytes).as_bytes()
    }

    /// Verify that the stored hash matches the content.
    pub fn verify_hash(&self) -> bool {
        let computed = Self::compute_hash(
            &self.payload,
            &self.next,
            &self.refs,
            &self.clock,
            &self.author,
        );
        self.hash == computed
    }

    /// Serialize the entry to MessagePack bytes.
    ///
    /// Uses `expect()` because msgpack serialization of `#[derive(Serialize)]` structs
    /// with known types cannot fail in practice. Converting to `Result` would add API
    /// complexity for a failure mode that doesn't exist.
    pub fn to_bytes(&self) -> Vec<u8> {
        rmp_serde::to_vec(self).expect("entry serialization should not fail")
    }

    /// Deserialize an entry from MessagePack bytes.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, rmp_serde::decode::Error> {
        rmp_serde::from_slice(bytes)
    }

    /// Return the hash as a hex string (for display/debugging).
    pub fn hash_hex(&self) -> String {
        hex::encode(self.hash)
    }
}

/// Encode a hash as hex string. Utility for display.
pub fn hash_hex(hash: &Hash) -> String {
    hex::encode(hash)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ontology::{EdgeTypeDef, NodeTypeDef, PropertyDef, ValueType};

    fn sample_ontology() -> Ontology {
        Ontology {
            node_types: BTreeMap::from([
                (
                    "entity".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::from([
                            (
                                "ip".into(),
                                PropertyDef {
                                    value_type: ValueType::String,
                                    required: false,
                                    description: None,
                                    constraints: None,
                                },
                            ),
                            (
                                "port".into(),
                                PropertyDef {
                                    value_type: ValueType::Int,
                                    required: false,
                                    description: None,
                                    constraints: None,
                                },
                            ),
                        ]),
                        subtypes: None,
                        parent_type: None,
                    },
                ),
                (
                    "signal".into(),
                    NodeTypeDef {
                        description: None,
                        properties: BTreeMap::new(),
                        subtypes: None,
                        parent_type: None,
                    },
                ),
            ]),
            edge_types: BTreeMap::from([(
                "RUNS_ON".into(),
                EdgeTypeDef {
                    description: None,
                    source_types: vec!["entity".into()],
                    target_types: vec!["entity".into()],
                    properties: BTreeMap::new(),
                },
            )]),
        }
    }

    fn sample_op() -> GraphOp {
        GraphOp::AddNode {
            node_id: "server-1".into(),
            node_type: "entity".into(),
            label: "Production Server".into(),
            properties: BTreeMap::from([
                ("ip".into(), Value::String("10.0.0.1".into())),
                ("port".into(), Value::Int(8080)),
            ]),
            subtype: None,
        }
    }

    fn sample_clock() -> LamportClock {
        LamportClock::with_values("inst-a", 1, 0)
    }

    fn sample_entry() -> Entry {
        Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a")
    }

    #[test]
    fn entry_hash_deterministic() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let e2 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        assert_eq!(e1.hash, e2.hash);
    }

    #[test]
    fn entry_hash_changes_on_mutation() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let different_op = GraphOp::AddNode {
            node_id: "server-2".into(),
            node_type: "entity".into(),
            label: "Other Server".into(),
            properties: BTreeMap::new(),
            subtype: None,
        };
        let e2 = Entry::new(different_op, vec![], vec![], sample_clock(), "inst-a");
        assert_ne!(e1.hash, e2.hash);
    }

    #[test]
    fn entry_hash_changes_with_different_author() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let e2 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-b");
        assert_ne!(e1.hash, e2.hash);
    }

    #[test]
    fn entry_hash_changes_with_different_clock() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let mut clock2 = sample_clock();
        clock2.physical_ms = 99;
        let e2 = Entry::new(sample_op(), vec![], vec![], clock2, "inst-a");
        assert_ne!(e1.hash, e2.hash);
    }

    #[test]
    fn entry_hash_changes_with_different_next() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let e2 = Entry::new(
            sample_op(),
            vec![[0u8; 32]],
            vec![],
            sample_clock(),
            "inst-a",
        );
        assert_ne!(e1.hash, e2.hash);
    }

    #[test]
    fn entry_verify_hash_valid() {
        let entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        assert!(entry.verify_hash());
    }

    #[test]
    fn entry_verify_hash_reject_tampered() {
        let mut entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        entry.author = "evil-node".into();
        assert!(!entry.verify_hash());
    }

    #[test]
    fn entry_roundtrip_msgpack() {
        let entry = Entry::new(
            sample_op(),
            vec![[1u8; 32]],
            vec![[2u8; 32]],
            sample_clock(),
            "inst-a",
        );
        let bytes = entry.to_bytes();
        let decoded = Entry::from_bytes(&bytes).unwrap();
        assert_eq!(entry, decoded);
    }

    #[test]
    fn entry_next_links_causal() {
        let e1 = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let e2 = Entry::new(
            GraphOp::RemoveNode {
                node_id: "server-1".into(),
            },
            vec![e1.hash],
            vec![],
            LamportClock::with_values("inst-a", 2, 0),
            "inst-a",
        );
        assert_eq!(e2.next, vec![e1.hash]);
        assert!(e2.verify_hash());
    }

    #[test]
    fn graphop_all_variants_serialize() {
        let ops = vec![
            GraphOp::DefineOntology {
                ontology: sample_ontology(),
            },
            sample_op(),
            GraphOp::AddEdge {
                edge_id: "e1".into(),
                edge_type: "RUNS_ON".into(),
                source_id: "svc-1".into(),
                target_id: "server-1".into(),
                properties: BTreeMap::new(),
            },
            GraphOp::UpdateProperty {
                entity_id: "server-1".into(),
                key: "cpu".into(),
                value: Value::Float(85.5),
            },
            GraphOp::RemoveNode {
                node_id: "server-1".into(),
            },
            GraphOp::RemoveEdge {
                edge_id: "e1".into(),
            },
            GraphOp::ExtendOntology {
                extension: crate::ontology::OntologyExtension {
                    node_types: BTreeMap::from([(
                        "metric".into(),
                        NodeTypeDef {
                            description: Some("A metric observation".into()),
                            properties: BTreeMap::new(),
                            subtypes: None,
                            parent_type: None,
                        },
                    )]),
                    edge_types: BTreeMap::new(),
                    node_type_updates: BTreeMap::new(),
                    edge_type_updates: BTreeMap::new(),
                },
            },
            GraphOp::Checkpoint {
                ops: vec![
                    GraphOp::DefineOntology {
                        ontology: sample_ontology(),
                    },
                    GraphOp::AddNode {
                        node_id: "n1".into(),
                        node_type: "entity".into(),
                        subtype: None,
                        label: "Node 1".into(),
                        properties: BTreeMap::new(),
                    },
                ],
                op_clocks: vec![(1, 0), (2, 0)],
                compacted_at_physical_ms: 1000,
                compacted_at_logical: 5,
            },
        ];
        for op in ops {
            let entry = Entry::new(op, vec![], vec![], sample_clock(), "inst-a");
            let bytes = entry.to_bytes();
            let decoded = Entry::from_bytes(&bytes).unwrap();
            assert_eq!(entry, decoded);
        }
    }

    #[test]
    fn genesis_entry_contains_ontology() {
        let ont = sample_ontology();
        let genesis = Entry::new(
            GraphOp::DefineOntology {
                ontology: ont.clone(),
            },
            vec![],
            vec![],
            LamportClock::new("inst-a"),
            "inst-a",
        );
        match &genesis.payload {
            GraphOp::DefineOntology { ontology } => assert_eq!(ontology, &ont),
            _ => panic!("genesis should be DefineOntology"),
        }
        assert!(genesis.next.is_empty(), "genesis has no predecessors");
        assert!(genesis.verify_hash());
    }

    #[test]
    fn value_all_variants_roundtrip() {
        let values = vec![
            Value::Null,
            Value::Bool(true),
            Value::Int(42),
            Value::Float(3.14),
            Value::String("hello".into()),
            Value::List(vec![Value::Int(1), Value::String("two".into())]),
            Value::Map(BTreeMap::from([("key".into(), Value::Bool(false))])),
        ];
        for val in values {
            let bytes = rmp_serde::to_vec(&val).unwrap();
            let decoded: Value = rmp_serde::from_slice(&bytes).unwrap();
            assert_eq!(val, decoded);
        }
    }

    #[test]
    fn hash_hex_format() {
        let entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let hex = entry.hash_hex();
        assert_eq!(hex.len(), 64);
        assert!(hex.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn unsigned_entry_has_no_signature() {
        let entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        assert!(!entry.is_signed());
        assert!(entry.signature.is_none());
    }

    #[test]
    fn unsigned_entry_roundtrip_preserves_none_signature() {
        let entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");
        let bytes = entry.to_bytes();
        let decoded = Entry::from_bytes(&bytes).unwrap();
        assert_eq!(decoded.signature, None);
        assert!(decoded.verify_hash());
    }

    #[cfg(feature = "signing")]
    mod signing_tests {
        use super::*;

        fn test_keypair() -> ed25519_dalek::SigningKey {
            use rand::rngs::OsRng;
            ed25519_dalek::SigningKey::generate(&mut OsRng)
        }

        #[test]
        fn signed_entry_roundtrip() {
            let key = test_keypair();
            let entry =
                Entry::new_signed(sample_op(), vec![], vec![], sample_clock(), "inst-a", &key);

            assert!(entry.is_signed());
            assert!(entry.verify_hash());

            let public = key.verifying_key();
            assert!(entry.verify_signature(&public));
        }

        #[test]
        fn signed_entry_serialization_roundtrip() {
            let key = test_keypair();
            let entry =
                Entry::new_signed(sample_op(), vec![], vec![], sample_clock(), "inst-a", &key);

            let bytes = entry.to_bytes();
            let decoded = Entry::from_bytes(&bytes).unwrap();

            assert!(decoded.is_signed());
            assert!(decoded.verify_hash());
            assert!(decoded.verify_signature(&key.verifying_key()));
        }

        #[test]
        fn wrong_key_fails_verification() {
            let key1 = test_keypair();
            let key2 = test_keypair();

            let entry =
                Entry::new_signed(sample_op(), vec![], vec![], sample_clock(), "inst-a", &key1);

            // Correct key verifies
            assert!(entry.verify_signature(&key1.verifying_key()));
            // Wrong key fails
            assert!(!entry.verify_signature(&key2.verifying_key()));
        }

        #[test]
        fn tampered_hash_fails_both_checks() {
            let key = test_keypair();
            let mut entry =
                Entry::new_signed(sample_op(), vec![], vec![], sample_clock(), "inst-a", &key);

            // Tamper with the hash
            entry.hash[0] ^= 0xFF;

            assert!(!entry.verify_hash());
            assert!(!entry.verify_signature(&key.verifying_key()));
        }

        #[test]
        fn unsigned_entry_passes_signature_check() {
            // D-027 backward compat: unsigned entries are accepted
            let key = test_keypair();
            let entry = Entry::new(sample_op(), vec![], vec![], sample_clock(), "inst-a");

            assert!(!entry.is_signed());
            assert!(entry.verify_signature(&key.verifying_key())); // returns true (no sig = ok)
        }
    }

    // -- Value JSON round-trip tests (Review 4, Issue #1) --

    #[test]
    fn value_int_json_roundtrip_preserves_type() {
        let val = Value::Int(1);
        let json = serde_json::to_string(&val).unwrap();
        let back: Value = serde_json::from_str(&json).unwrap();
        assert_eq!(
            back,
            Value::Int(1),
            "Int(1) -> JSON -> back should stay Int, got {:?}",
            back
        );
    }

    #[test]
    fn value_float_json_roundtrip_preserves_type() {
        let val = Value::Float(1.0);
        let json = serde_json::to_string(&val).unwrap();
        let back: Value = serde_json::from_str(&json).unwrap();
        assert_eq!(
            back,
            Value::Float(1.0),
            "Float(1.0) -> JSON -> back should stay Float, got {:?}",
            back
        );
    }

    #[test]
    fn value_float_json_includes_decimal() {
        // serde_json must serialize 1.0_f64 as "1.0" (not "1")
        // to ensure untagged deserialization picks Float, not Int
        let json = serde_json::to_string(&Value::Float(1.0)).unwrap();
        assert!(
            json.contains('.'),
            "Float(1.0) must serialize with decimal point, got: {}",
            json
        );
    }

    #[test]
    fn graphop_with_mixed_values_json_roundtrip() {
        let mut props = BTreeMap::new();
        props.insert("count".into(), Value::Int(42));
        props.insert("ratio".into(), Value::Float(1.0));
        props.insert("name".into(), Value::String("test".into()));

        let op = GraphOp::UpdateProperty {
            entity_id: "e1".into(),
            key: "data".into(),
            value: Value::Map(props),
        };

        let json = serde_json::to_string(&op).unwrap();
        let back: GraphOp = serde_json::from_str(&json).unwrap();

        // Verify the round-tripped op produces the same hash
        let entry1 = Entry::new(op, vec![], vec![], sample_clock(), "a");
        let entry2 = Entry::new(back, vec![], vec![], sample_clock(), "a");
        assert_eq!(
            entry1.hash, entry2.hash,
            "JSON round-trip changed the hash!"
        );
    }

    // -- Wire format compatibility (S4) --

    /// A legacy 8-element entry, as written by every build up to 0.2.7, must
    /// still deserialize after `ontology_hash` was removed. This is the eta
    /// case: an existing redb store opened by a newer binary.
    #[test]
    fn legacy_eight_element_entry_still_deserializes() {
        let entry = sample_entry();
        // Rebuild the legacy shape by hand: the same seven fields plus the
        // trailing ontology_hash that used to follow. Tuples serialize as
        // positional arrays, which is exactly the old struct encoding.
        let legacy = (
            entry.hash,
            entry.payload.clone(),
            entry.next.clone(),
            entry.refs.clone(),
            entry.clock.clone(),
            entry.author.clone(),
            entry.signature.clone(),
            None::<Hash>,
        );
        let bytes = rmp_serde::to_vec(&legacy).unwrap();
        assert_eq!(bytes[0], 0x98, "legacy fixture is not 8 elements");

        let restored = Entry::from_bytes(&bytes).expect("legacy entry must load");
        assert_eq!(restored, entry);
        assert!(restored.verify_hash());
    }

    /// The stream must stay aligned: a stray trailing element would corrupt
    /// every entry after it, since entries are read as a `Vec<Entry>`.
    #[test]
    fn legacy_entries_in_a_sequence_stay_aligned() {
        let entry = sample_entry();
        let legacy = |e: &Entry| {
            (
                e.hash,
                e.payload.clone(),
                e.next.clone(),
                e.refs.clone(),
                e.clock.clone(),
                e.author.clone(),
                e.signature.clone(),
                None::<Hash>,
            )
        };
        let bytes = rmp_serde::to_vec(&vec![legacy(&entry), legacy(&entry)]).unwrap();
        let restored: Vec<Entry> = rmp_serde::from_slice(&bytes).expect("legacy vec must load");
        assert_eq!(restored.len(), 2);
        assert!(restored.iter().all(|e| e.verify_hash()));
    }

    /// Entries serialize as a POSITIONAL msgpack array — no field names on the
    /// wire or on disk. Adding or removing a field therefore changes the arity
    /// of every entry ever written, breaking stored redb data and any peer on
    /// an older build. That is a protocol decision, never a cleanup.
    ///
    /// This test exists because S4's "delete the dead field" read as tidying
    /// and was in fact format-load-bearing.
    #[test]
    fn entry_wire_format_is_a_positional_array_of_seven() {
        let bytes = sample_entry().to_bytes();

        // msgpack fixarray marker: 0x90 | len. Seven fields => 0x97.
        assert_eq!(
            bytes[0], 0x97,
            "Entry is no longer a 7-element positional array. Changing the \
             field count breaks every persisted store and every peer running \
             an older build; add a deserialization shim for the old arity \
             (as S4 did for the 8-element form) before changing this."
        );
        // No field names present, confirming positional encoding: there is
        // nowhere for an added or removed field to hide.
        for name in [b"payload".as_slice(), b"signature".as_slice()] {
            assert!(
                !bytes.windows(name.len()).any(|w| w == name),
                "expected positional encoding, found a field name on the wire"
            );
        }
    }

    // -- DefineLens variant --

    #[test]
    fn define_lens_roundtrips() {
        let op = GraphOp::DefineLens {
            transforms: vec![1, 2, 3, 4],
        };
        let entry = Entry::new(op.clone(), vec![], vec![], sample_clock(), "author");
        let bytes = entry.to_bytes();
        let restored = Entry::from_bytes(&bytes).unwrap();
        assert_eq!(restored.payload, op);
        assert!(restored.verify_hash());
    }
}

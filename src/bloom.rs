use serde::{Deserialize, Serialize};

use crate::entry::Hash;

/// A simple Bloom filter for sync negotiation.
///
/// Used during sync to quickly determine which entries a peer likely has.
/// False positives are expected (~1% with default parameters); false negatives
/// never occur. Subsequent sync rounds resolve false positives via explicit
/// `need` lists.
///
/// Parameters: `num_bits` total bits in the bitvec, `num_hashes` hash functions
/// (derived from BLAKE3 by slicing the 32-byte hash into segments).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BloomFilter {
    bits: Vec<u64>,
    num_bits: usize,
    num_hashes: u32,
    count: usize,
}

impl BloomFilter {
    /// S-05: Validate bloom filter dimensions after deserialization.
    /// Prevents panics from malformed sync offers (division by zero, out of bounds).
    pub fn validate(&self) -> Result<(), String> {
        if self.num_bits == 0 {
            return Err("bloom filter num_bits must be > 0".into());
        }
        if self.bits.len() * 64 < self.num_bits {
            return Err(format!(
                "bloom filter bits array too small: {} words for {} bits",
                self.bits.len(),
                self.num_bits
            ));
        }
        if self.num_hashes == 0 || self.num_hashes > 32 {
            return Err(format!(
                "bloom filter num_hashes {} out of range [1, 32]",
                self.num_hashes
            ));
        }
        Ok(())
    }

    /// Create a new Bloom filter sized for `expected_items` with the given
    /// false positive rate.
    ///
    /// Computes optimal bit count and hash count from the standard formulas:
    /// - m = -n * ln(p) / (ln(2)^2)
    /// - k = (m/n) * ln(2)
    pub fn new(expected_items: usize, fp_rate: f64) -> Self {
        assert!(expected_items > 0, "expected_items must be > 0");
        assert!((0.0..1.0).contains(&fp_rate), "fp_rate must be in (0, 1)");

        let n = expected_items as f64;
        let ln2 = std::f64::consts::LN_2;
        let ln2_sq = ln2 * ln2;

        let num_bits = ((-n * fp_rate.ln()) / ln2_sq).ceil() as usize;
        let num_bits = num_bits.max(64); // minimum 64 bits
        let num_hashes = ((num_bits as f64 / n) * ln2).ceil() as u32;
        let num_hashes = num_hashes.max(1);

        let words = num_bits.div_ceil(64);
        Self {
            bits: vec![0u64; words],
            num_bits,
            num_hashes,
            count: 0,
        }
    }

    /// Insert a hash into the filter.
    pub fn insert(&mut self, hash: &Hash) {
        for idx in self.indices(hash) {
            let word = idx / 64;
            let bit = idx % 64;
            self.bits[word] |= 1u64 << bit;
        }
        self.count += 1;
    }

    /// Check if a hash might be in the filter.
    ///
    /// Returns `true` if the item is probably present (with false positive rate),
    /// `false` if the item is definitely NOT present.
    pub fn contains(&self, hash: &Hash) -> bool {
        for idx in self.indices(hash) {
            let word = idx / 64;
            let bit = idx % 64;
            if self.bits[word] & (1u64 << bit) == 0 {
                return false;
            }
        }
        true
    }

    /// Number of items inserted.
    pub fn count(&self) -> usize {
        self.count
    }

    /// Merge another filter into this one (union).
    ///
    /// Both filters must have the same dimensions (num_bits, num_hashes).
    pub fn merge(&mut self, other: &BloomFilter) {
        assert_eq!(self.num_bits, other.num_bits, "bloom filter size mismatch");
        assert_eq!(
            self.num_hashes, other.num_hashes,
            "bloom filter hash count mismatch"
        );
        for (a, b) in self.bits.iter_mut().zip(other.bits.iter()) {
            *a |= *b;
        }
        self.count += other.count;
    }

    /// Serialize to MessagePack bytes.
    pub fn to_bytes(&self) -> Vec<u8> {
        rmp_serde::to_vec(self).expect("bloom filter serialization should not fail")
    }

    /// Deserialize from MessagePack bytes.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, rmp_serde::decode::Error> {
        rmp_serde::from_slice(bytes)
    }

    /// Compute the bit indices for a given hash.
    ///
    /// Uses enhanced double hashing: h_i = h1 + i*h2 + i^2 (mod num_bits)
    /// where h1 and h2 are derived from the first 16 bytes of the BLAKE3 hash.
    fn indices(&self, hash: &Hash) -> Vec<usize> {
        // Split the 32-byte hash into two 8-byte values for double hashing.
        let h1 = u64::from_le_bytes(hash[0..8].try_into().unwrap());
        let h2 = u64::from_le_bytes(hash[8..16].try_into().unwrap());
        let m = self.num_bits as u64;

        (0..self.num_hashes)
            .map(|i| {
                let i = i as u64;
                // Enhanced double hashing with quadratic probing
                let idx = h1
                    .wrapping_add(i.wrapping_mul(h2))
                    .wrapping_add(i.wrapping_mul(i));
                (idx % m) as usize
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_hash(seed: u8) -> Hash {
        let mut h = [0u8; 32];
        h[0] = seed;
        // Use BLAKE3 to get a proper distribution
        *blake3::hash(&h).as_bytes()
    }

    #[test]
    fn bloom_insert_and_check() {
        let mut bloom = BloomFilter::new(100, 0.01);
        let h1 = make_hash(1);
        let h2 = make_hash(2);
        let h3 = make_hash(3);

        bloom.insert(&h1);
        bloom.insert(&h2);

        assert!(bloom.contains(&h1));
        assert!(bloom.contains(&h2));
        // h3 was not inserted — should (almost certainly) not be found
        // Note: this could theoretically fail with a false positive,
        // but with FPR 0.01 and only 2 items in a 100-item filter, it's vanishingly unlikely.
        assert!(!bloom.contains(&h3));
    }

    #[test]
    fn bloom_empty_contains_nothing() {
        let bloom = BloomFilter::new(100, 0.01);
        for i in 0..=255 {
            assert!(!bloom.contains(&make_hash(i)));
        }
    }

    #[test]
    fn bloom_false_positive_rate() {
        // Insert 1000 items, check 10000 non-inserted items.
        // FPR target: 1%. Allow up to 2% for statistical variance.
        let n = 1000;
        let mut bloom = BloomFilter::new(n, 0.01);

        for i in 0..n {
            let h = *blake3::hash(&(i as u64).to_le_bytes()).as_bytes();
            bloom.insert(&h);
        }

        let test_count = 10_000;
        let mut false_positives = 0;
        for i in n..(n + test_count) {
            let h = *blake3::hash(&(i as u64).to_le_bytes()).as_bytes();
            if bloom.contains(&h) {
                false_positives += 1;
            }
        }

        let fpr = false_positives as f64 / test_count as f64;
        assert!(
            fpr < 0.02,
            "false positive rate {fpr:.4} exceeds 2% threshold"
        );
    }

    #[test]
    fn bloom_merge_union() {
        let mut bloom_a = BloomFilter::new(100, 0.01);
        let mut bloom_b = BloomFilter::new(100, 0.01);

        let h1 = make_hash(1);
        let h2 = make_hash(2);
        let h3 = make_hash(3);

        bloom_a.insert(&h1);
        bloom_a.insert(&h2);
        bloom_b.insert(&h2);
        bloom_b.insert(&h3);

        bloom_a.merge(&bloom_b);

        // Merged filter should contain all three
        assert!(bloom_a.contains(&h1));
        assert!(bloom_a.contains(&h2));
        assert!(bloom_a.contains(&h3));
    }

    #[test]
    fn bloom_serialization_roundtrip() {
        let mut bloom = BloomFilter::new(100, 0.01);
        let h1 = make_hash(1);
        let h2 = make_hash(2);
        bloom.insert(&h1);
        bloom.insert(&h2);

        let bytes = bloom.to_bytes();
        let restored = BloomFilter::from_bytes(&bytes).unwrap();

        assert!(restored.contains(&h1));
        assert!(restored.contains(&h2));
        assert!(!restored.contains(&make_hash(3)));
        assert_eq!(restored.count(), 2);
        assert_eq!(restored.num_bits, bloom.num_bits);
        assert_eq!(restored.num_hashes, bloom.num_hashes);
    }

    #[test]
    fn bloom_count_tracks_inserts() {
        let mut bloom = BloomFilter::new(100, 0.01);
        assert_eq!(bloom.count(), 0);
        bloom.insert(&make_hash(1));
        assert_eq!(bloom.count(), 1);
        bloom.insert(&make_hash(2));
        assert_eq!(bloom.count(), 2);
    }
}

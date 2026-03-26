.PHONY: fmt clippy test pytest examples check build

fmt:
	cargo fmt --check

clippy:
	cargo clippy --no-default-features -- -D warnings

test:
	cargo test --no-default-features

pytest:
	pytest pytests/ -v

examples:
	python examples/offline_first.py
	python examples/partition_heal.py
	python examples/concurrent_writes.py
	python examples/ring_topology.py

# Full CI mirror — run before pushing
check: fmt clippy test build pytest

# Build Python wheel (development mode)
build:
	maturin develop --release --features python

# Contributing to Silk

Contributions are welcome. Here's how to get started.

## Development Setup

```bash
# Clone
git clone https://github.com/Kieleth/silk-graph.git
cd silk-graph

# Rust
cargo test --all-features

# Python (requires maturin)
pip install maturin pytest
maturin develop --release
pytest pytests/ -v

# Benchmarks
cargo bench
```

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Run `cargo test`, `cargo clippy`, `cargo fmt`, and `pytest`
4. Keep PRs focused — one feature or fix per PR

## Code Style

- Rust: `cargo fmt` (default rustfmt settings)
- Rust: `cargo clippy -- -D warnings` (zero warnings)
- Python: follow existing patterns in `pytests/`

## Architecture

Silk's architecture is documented in [DESIGN.md](DESIGN.md). Decisions are numbered D-001 through D-028 (R-01/HLC, R-02/Quarantine, R-03/Schema Evolution complete). New features that change the architecture should propose a new decision number. Roadmap features (R-001 through R-008) are tracked in [ROADMAP.md](ROADMAP.md).

To run tests with signing support:
```bash
cargo test --features signing
```

## License

By contributing, you agree that your contributions will be licensed under the MIT OR Apache-2.0 dual license.

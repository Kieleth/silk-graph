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

Design decisions are numbered D-001 through D-028 in [DESIGN.md](DESIGN.md). Roadmap items R-01 through R-08 are tracked in [ROADMAP.md](ROADMAP.md) (R-01 through R-08 complete (all roadmap items done)). To run tests with signing: `cargo test --features signing`

## License

By contributing, you agree that your contributions will be licensed under the MIT OR Apache-2.0 dual license.

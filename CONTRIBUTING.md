# Contributing to Silk

Contributions are welcome. Here's how to get started.

## Development Setup

```bash
# Clone
git clone https://github.com/Kieleth/silk-graph.git
cd silk-graph

# Build Python wheel (development mode)
pip install maturin pytest
maturin develop --release --features python

# Run full CI check locally (fmt + clippy + rust tests + pytest)
make check

# Or run individual steps
make fmt       # cargo fmt --check
make clippy    # cargo clippy --no-default-features -- -D warnings
make test      # cargo test --no-default-features
make pytest    # pytest pytests/ -v
make examples  # run all example scripts

# Benchmarks
cargo bench
```

A pre-push git hook runs `make check` automatically before every push. Skip with `git push --no-verify` when needed.

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Run `make check` (mirrors CI exactly)
4. Keep PRs focused — one feature or fix per PR

## Release Discipline

Silk releases follow a few conventions that keep the track record honest:

- **Every `Fixed` entry in `CHANGELOG.md` should reference the regression test that would have caught the bug.** If a fix doesn't have a test that prevents the class of error from recurring, the fix isn't done. The pattern: `see: pytests/test_X.py::test_Y` or `experiments/test_Z.py::test_W`.
- **Convergence- and invariant-level bugs get an entry in the [Correctness track record](CHANGELOG.md#correctness-track-record) table** at the top of the CHANGELOG. One row per bug, with bug class, detection mechanism, and the regression test. Performance fixes, ergonomic fixes, and doc fixes do NOT go in the track record — only correctness.
- **A release never ships with known-failing tests or a skipped `make check`.** The pre-push hook is not a suggestion.
- **Version bumps reflect scope honestly.** Patch for bug fixes and documentation. Minor for new capabilities. Major for breaking wire-format or API changes. We err on the side of bumping the minor when in doubt.
- **Each release is a tagged commit** (`vX.Y.Z`), which triggers the release workflow: wheel build for Linux/macOS/Windows, PyPI publish via trusted publishing, crates.io publish, GitHub release with changelog notes.

## Code Style

- Rust: `cargo fmt` (default rustfmt settings)
- Rust: `cargo clippy -- -D warnings` (zero warnings)
- Python: follow existing patterns in `pytests/`

## Architecture

Design decisions are numbered D-001 through D-028 in [DESIGN.md](DESIGN.md). Roadmap items R-01 through R-08 are tracked in [ROADMAP.md](ROADMAP.md) (R-01 through R-08 complete (all roadmap items done)). To run tests with signing: `cargo test --features signing`

## License

By contributing, you agree that your contributions will be licensed under the MIT OR Apache-2.0 dual license.

"""Gate: every formal claim is either tested, TLA+-modeled, or explicitly out of scope.

Wraps scripts/audit_claims.py. Fails if:
  - any claim in PROOF.md / INVARIANTS.md has zero verification surfaces, or
  - any TLA+-eligible invariant/theorem has no spec reference.

Runs as part of the normal pytest suite so drift is caught in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import audit_claims  # noqa: E402


@pytest.fixture(scope="module")
def report() -> dict:
    return audit_claims.build_report()


def test_every_claim_has_some_coverage(report: dict) -> None:
    """No claim may live in docs without at least one verification surface."""
    uncovered = [
        c for c in report["claims"]
        if not (c["rust_tests"] or c["python_tests"] or c["tla_specs"])
    ]
    assert not uncovered, (
        f"Claims with zero coverage: {[c['claim_id'] for c in uncovered]}. "
        f"Either add a test/spec reference or remove the claim."
    )


def test_all_tla_eligible_claims_are_formalized(report: dict) -> None:
    """Every invariant/theorem not in TLA_INELIGIBLE must be in a formal/*.tla spec."""
    gaps = [
        c for c in report["claims"]
        if c["tla_eligible"]
        and not c["tla_specs"]
        and c["kind"] in ("invariant", "theorem")
    ]
    assert not gaps, (
        f"PROOF.md claims eligible for TLA+ but not modeled: "
        f"{[c['claim_id'] for c in gaps]}. Extend formal/*.tla or mark ineligible "
        f"with rationale in scripts/audit_claims.py::TLA_INELIGIBLE."
    )


def test_audit_json_is_up_to_date(report: dict) -> None:
    """formal/audit.json must match the live audit. Regenerate by running the script."""
    import json

    snapshot_path = ROOT / "formal" / "audit.json"
    assert snapshot_path.exists(), (
        "formal/audit.json missing. Run: python scripts/audit_claims.py"
    )
    on_disk = json.loads(snapshot_path.read_text())
    assert on_disk == report, (
        "formal/audit.json is stale. Run: python scripts/audit_claims.py "
        "and commit the updated file."
    )

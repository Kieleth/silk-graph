"""Audit Silk's verifiable claims across docs, tests, and formal specs.

Scans PROOF.md and INVARIANTS.md for claim identifiers (I-01, Theorem 3, INV-2,
...), then greps Rust tests, Python tests, and TLA+ specs for references to
each. Produces formal/audit.json + a terminal summary. Exits non-zero if any
claim has zero verification surfaces.

Run: python scripts/audit_claims.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CLAIM_PATTERNS = {
    "invariant": re.compile(r"\bI-0[1-9]\b"),
    "theorem": re.compile(r"\bTheorem [1-9]\b"),
    "inv": re.compile(r"\bINV-[1-9]\b"),
}

# Claims explicitly out of TLA+ scope, with rationale. These won't show up as
# "formalizable gaps" in the summary.
TLA_INELIGIBLE = {
    "I-01": "cryptographic hash integrity; verified by unit test, not structural reasoning",
    "I-06": "quarantine determinism is a corollary of Theorem 3 (PROOF.md §6)",
}

SOURCES = {
    "invariant": [ROOT / "PROOF.md"],
    "theorem": [ROOT / "PROOF.md"],
    "inv": [ROOT / "INVARIANTS.md"],
}

REFERENCE_SCAN = {
    "rust_tests": sorted(ROOT.glob("src/**/*.rs")) + sorted(ROOT.glob("tests/**/*.rs")),
    "python_tests": sorted(ROOT.glob("pytests/**/*.py")),
    "tla_specs": sorted(ROOT.glob("formal/*.tla")) + sorted(ROOT.glob("formal/*.md")),
}


@dataclass
class ClaimCoverage:
    claim_id: str
    kind: str
    rust_tests: list[str]
    python_tests: list[str]
    tla_specs: list[str]
    tla_eligible: bool
    tla_ineligible_reason: str | None

    @property
    def covered(self) -> bool:
        return bool(self.rust_tests or self.python_tests or self.tla_specs)

    @property
    def surface_count(self) -> int:
        return sum(
            1 for s in (self.rust_tests, self.python_tests, self.tla_specs) if s
        )


def extract_claims() -> dict[str, str]:
    """Return {claim_id: kind} across all sources."""
    claims: dict[str, str] = {}
    for kind, paths in SOURCES.items():
        pattern = CLAIM_PATTERNS[kind]
        for path in paths:
            if not path.exists():
                continue
            text = path.read_text()
            for match in pattern.findall(text):
                claims[match] = kind
    return claims


def scan_references(claim_id: str) -> dict[str, list[str]]:
    """Return references to claim_id grouped by surface."""
    refs: dict[str, list[str]] = {k: [] for k in REFERENCE_SCAN}
    pattern = re.compile(rf"\b{re.escape(claim_id)}\b")
    for surface, paths in REFERENCE_SCAN.items():
        for path in paths:
            try:
                if pattern.search(path.read_text()):
                    refs[surface].append(str(path.relative_to(ROOT)))
            except (UnicodeDecodeError, OSError):
                continue
    return refs


def build_report() -> dict:
    claims = extract_claims()
    coverages = []
    for claim_id, kind in sorted(claims.items()):
        refs = scan_references(claim_id)
        coverages.append(
            ClaimCoverage(
                claim_id=claim_id,
                kind=kind,
                rust_tests=refs["rust_tests"],
                python_tests=refs["python_tests"],
                tla_specs=refs["tla_specs"],
                tla_eligible=claim_id not in TLA_INELIGIBLE,
                tla_ineligible_reason=TLA_INELIGIBLE.get(claim_id),
            )
        )

    total = len(coverages)
    covered = sum(1 for c in coverages if c.covered)
    tla_covered = sum(1 for c in coverages if c.tla_specs)
    test_covered = sum(1 for c in coverages if c.rust_tests or c.python_tests)
    tla_eligible_total = sum(
        1 for c in coverages if c.tla_eligible and c.kind in ("invariant", "theorem")
    )
    tla_eligible_covered = sum(
        1 for c in coverages
        if c.tla_eligible and c.tla_specs and c.kind in ("invariant", "theorem")
    )

    by_kind: dict[str, dict[str, int]] = {}
    for c in coverages:
        bucket = by_kind.setdefault(
            c.kind, {"total": 0, "covered": 0, "tla": 0, "tests": 0}
        )
        bucket["total"] += 1
        if c.covered:
            bucket["covered"] += 1
        if c.tla_specs:
            bucket["tla"] += 1
        if c.rust_tests or c.python_tests:
            bucket["tests"] += 1

    return {
        "summary": {
            "total_claims": total,
            "covered": covered,
            "tla_covered": tla_covered,
            "test_covered": test_covered,
            "coverage_pct": round(100 * covered / total, 1) if total else 0.0,
            "tla_pct": round(100 * tla_covered / total, 1) if total else 0.0,
            "tla_eligible_total": tla_eligible_total,
            "tla_eligible_covered": tla_eligible_covered,
            "tla_eligible_pct": (
                round(100 * tla_eligible_covered / tla_eligible_total, 1)
                if tla_eligible_total else 0.0
            ),
            "by_kind": by_kind,
        },
        "claims": [asdict(c) for c in coverages],
    }


def print_summary(report: dict) -> None:
    s = report["summary"]
    print(f"Silk claim coverage audit")
    print(f"=" * 50)
    print(f"Total claims:   {s['total_claims']}")
    print(f"Any coverage:   {s['covered']}/{s['total_claims']} ({s['coverage_pct']}%)")
    print(f"Test coverage:  {s['test_covered']}/{s['total_claims']}")
    print(
        f"TLA+ (eligible): {s['tla_eligible_covered']}/{s['tla_eligible_total']} "
        f"({s['tla_eligible_pct']}%)"
    )
    print()
    print("By kind:")
    for kind, b in sorted(s["by_kind"].items()):
        print(
            f"  {kind:10s} covered {b['covered']}/{b['total']} "
            f"(tla {b['tla']}, tests {b['tests']})"
        )
    print()

    uncovered = [c for c in report["claims"] if not (c["rust_tests"] or c["python_tests"] or c["tla_specs"])]
    if uncovered:
        print("UNCOVERED:")
        for c in uncovered:
            print(f"  {c['claim_id']} ({c['kind']})")
        print()

    no_tla_eligible = [
        c for c in report["claims"]
        if c["tla_eligible"]
        and not c["tla_specs"]
        and c["kind"] in ("invariant", "theorem")
    ]
    if no_tla_eligible:
        print("Formalizable (not yet in TLA+):")
        for c in no_tla_eligible:
            print(f"  {c['claim_id']}")
        print()

    ineligible = [c for c in report["claims"] if not c["tla_eligible"]]
    if ineligible:
        print("Out of TLA+ scope (by design):")
        for c in ineligible:
            print(f"  {c['claim_id']}: {c['tla_ineligible_reason']}")
        print()


def main() -> int:
    report = build_report()
    out_path = ROOT / "formal" / "audit.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print_summary(report)
    print(f"Wrote {out_path.relative_to(ROOT)}")

    uncovered = [
        c for c in report["claims"]
        if not (c["rust_tests"] or c["python_tests"] or c["tla_specs"])
    ]
    return 1 if uncovered else 0


if __name__ == "__main__":
    sys.exit(main())

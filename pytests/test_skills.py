"""The agent skills that ship inside the wheel.

They travel with the package so any environment that installs silk-graph can
install them without cloning the repo. That means a malformed skill ships
silently unless something checks, and a reference file renamed without
updating its SKILL.md becomes a dangling pointer the model follows into
nothing.
"""

import re
from pathlib import Path

import pytest

from silk import skills_cli

SKILLS = sorted(d for d in skills_cli.SKILLS_DIR.iterdir()
                if (d / "SKILL.md").is_file()) if skills_cli.SKILLS_DIR.is_dir() else []
SKILL_NAMES = [d.name for d in SKILLS]


def _frontmatter(skill_dir: Path) -> dict[str, str]:
    text = (skill_dir / "SKILL.md").read_text()
    assert text.startswith("---\n"), f"{skill_dir.name}: no frontmatter block"
    _, block, _ = text.split("---", 2)
    fields = {}
    for line in block.strip().splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def test_skills_are_bundled():
    """If this fails, the wheel ships no skills and `silk-skills install` is
    a no-op for everyone who pip-installed silk."""
    assert SKILLS, f"no skills found under {skills_cli.SKILLS_DIR}"
    assert "silk-graph" in SKILL_NAMES
    assert "silk-internals" in SKILL_NAMES


@pytest.mark.parametrize("skill_dir", SKILLS, ids=SKILL_NAMES)
def test_frontmatter_is_well_formed(skill_dir):
    fm = _frontmatter(skill_dir)
    assert fm.get("name") == skill_dir.name, (
        f"frontmatter name {fm.get('name')!r} != directory {skill_dir.name!r}; "
        "the harness resolves skills by directory")
    description = fm.get("description", "")
    assert len(description) > 40, "description is what decides when the skill loads"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=SKILL_NAMES)
def test_referenced_files_exist(skill_dir):
    """A SKILL.md that points at references/foo.md must have it. The model
    follows these pointers; a dangling one wastes a turn."""
    body = (skill_dir / "SKILL.md").read_text()
    for rel in set(re.findall(r"`(references/[\w./-]+\.md)`", body)):
        assert (skill_dir / rel).is_file(), f"{skill_dir.name}: dangling pointer {rel}"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=SKILL_NAMES)
def test_every_reference_is_reachable(skill_dir):
    """And the reverse: a reference nobody points at will never be read."""
    ref_dir = skill_dir / "references"
    if not ref_dir.is_dir():
        return
    body = (skill_dir / "SKILL.md").read_text()
    for ref in sorted(ref_dir.glob("*.md")):
        assert f"references/{ref.name}" in body, (
            f"{skill_dir.name}: {ref.name} is never referenced from SKILL.md")


def test_install_copies_skills_and_references(tmp_path):
    rc = skills_cli.main(["install", "--project", str(tmp_path)])
    assert rc == 0

    destination = tmp_path / ".claude" / "skills"
    for name in SKILL_NAMES:
        assert (destination / name / "SKILL.md").is_file()
    # References must survive the copy, not just the top-level file.
    assert (destination / "silk-graph" / "references" / "ontology.md").is_file()


def test_install_is_idempotent(tmp_path):
    """Re-run after every upgrade is the documented workflow, so a second run
    must overwrite cleanly rather than fail on an existing directory."""
    assert skills_cli.main(["install", "--project", str(tmp_path)]) == 0
    assert skills_cli.main(["install", "--project", str(tmp_path)]) == 0


def test_bad_subcommand_is_usage_error():
    assert skills_cli.main([]) == 2
    assert skills_cli.main(["nonsense"]) == 2

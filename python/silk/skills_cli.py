"""Install the agent skills that ship with silk-graph.

    silk-skills install --user          # ~/.claude/skills — every project here
    silk-skills install --project DIR   # DIR/.claude/skills (default: cwd)
    silk-skills list

The skills travel inside the wheel, so any environment that has silk-graph
installed can install them without cloning the repo. Re-run after upgrading:
the skills describe the installed version's behavior, and silk's traps change
between versions.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _bundled() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(d for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").is_file())


def _describe(skill_dir: Path) -> str:
    """First `description:` line of the frontmatter, trimmed for listing."""
    for line in (skill_dir / "SKILL.md").read_text().splitlines():
        if line.startswith("description:"):
            text = line.split(":", 1)[1].strip()
            return text if len(text) <= 100 else text[:97] + "..."
    return ""


def _install(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="silk-skills install",
        description="Copy silk's agent skills into a .claude/skills directory.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--user", action="store_true",
        help="install into ~/.claude/skills (available in every project on this machine)")
    target.add_argument(
        "--project", metavar="DIR", default=None,
        help="install into DIR/.claude/skills (default: current directory)")
    args = parser.parse_args(argv)

    skills = _bundled()
    if not skills:
        print(f"no bundled skills found at {SKILLS_DIR}", file=sys.stderr)
        return 1

    base = Path.home() if args.user else Path(args.project or ".")
    destination = base / ".claude" / "skills"
    destination.mkdir(parents=True, exist_ok=True)

    for skill_dir in skills:
        shutil.copytree(skill_dir, destination / skill_dir.name, dirs_exist_ok=True)
        print(f"installed skill: {skill_dir.name} -> {destination / skill_dir.name}")

    from silk import __version__
    print(f"\nThese describe silk-graph {__version__}. Re-run after upgrading.")
    return 0


def _list(_argv: list[str]) -> int:
    skills = _bundled()
    if not skills:
        print(f"no bundled skills found at {SKILLS_DIR}", file=sys.stderr)
        return 1
    for skill_dir in skills:
        print(f"{skill_dir.name}\n    {_describe(skill_dir)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"install": _install, "list": _list}
    if not argv or argv[0] not in commands:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return commands[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())

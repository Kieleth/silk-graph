"""Version consistency — silk.__version__ must match the installed package.

The version lives in three places: Cargo.toml, pyproject.toml, and
python/silk/__init__.py. The first two feed the build; this test pins the
third to the installed metadata so a bump can't miss it.
"""

from importlib.metadata import version

import silk


def test_version_matches_package_metadata():
    assert silk.__version__ == version("silk-graph")

"""FreshRoute LastWindow package."""
from __future__ import annotations
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent


class _DirectStatSubmoduleFinder(importlib.abc.MetaPathFinder):
    """Resolves app.* modules via direct os.path.isfile/isdir stat() calls so 9p readdir dcache never hides files."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith("app."):
            return None
        rel = fullname[len("app."):].replace(".", "/")
        pkg_init = _APP_DIR / rel / "__init__.py"
        if os.path.isfile(pkg_init):
            return importlib.util.spec_from_file_location(
                fullname,
                str(pkg_init),
                submodule_search_locations=[str(_APP_DIR / rel)],
            )
        mod_file = _APP_DIR / f"{rel}.py"
        if os.path.isfile(mod_file):
            return importlib.util.spec_from_file_location(fullname, str(mod_file))
        return None


if not any(isinstance(f, _DirectStatSubmoduleFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DirectStatSubmoduleFinder())

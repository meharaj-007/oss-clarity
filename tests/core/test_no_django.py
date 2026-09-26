"""`oss_clarity` and `oss_clarity.core` must import without Django.

CI also runs this file in an environment where Django is not installed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "src" / "oss_clarity" / "core"


def test_importing_core_does_not_import_django():
    code = (
        "import sys, oss_clarity, oss_clarity.core\n"
        "loaded = [m for m in sys.modules if m == 'django' or m.startswith('django.')]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_core_imports_only_the_standard_library_and_itself():
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in allowed, f"{path.name} imports {name}"

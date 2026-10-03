"""Offline regression checks for CI compilation and root CLI entrypoints.

These checks inspect source code only. They do not execute CLI main()
functions, authenticate, access the network, or place orders.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shlex


ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRS = (
    "ai",
    "fundamentals",
    "market_data",
    "news",
    "screening",
    "technical",
    "tests",
)

ROOT_CLIS = (
    "batch_scan.py",
    "campaign_history.py",
    "campaign_scan.py",
    "guard_log_audit.py",
    "guard_preflight.py",
    "main.py",
    "prefilter_scan.py",
    "request_budget.py",
    "research_digest.py",
)


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False

    condition = node.test

    return (
        isinstance(condition, ast.Compare)
        and isinstance(condition.left, ast.Name)
        and condition.left.id == "__name__"
        and len(condition.ops) == 1
        and isinstance(condition.ops[0], ast.Eq)
        and len(condition.comparators) == 1
        and isinstance(condition.comparators[0], ast.Constant)
        and condition.comparators[0].value == "__main__"
    )


def test_ci_compiles_source_directories_and_every_root_python_file():
    workflow = (
        ROOT / ".github" / "workflows" / "offline-tests.yml"
    ).read_text(encoding="utf-8")

    compile_commands = [
        line.strip()
        for line in workflow.splitlines()
        if line.strip().startswith("python -m compileall ")
    ]

    assert len(compile_commands) == 1

    actual = shlex.split(compile_commands[0])

    expected = [
        "python",
        "-m",
        "compileall",
        "-q",
        *SOURCE_DIRS,
        "*.py",
    ]

    assert actual == expected

    for directory in SOURCE_DIRS:
        assert (ROOT / directory).is_dir(), directory

    root_python_files = {
        path.name for path in ROOT.glob("*.py")
    }

    assert set(ROOT_CLIS).issubset(root_python_files)
    assert "config.py" in root_python_files


def test_all_root_command_scripts_have_explicit_main_guards():
    for filename in ROOT_CLIS:
        path = ROOT / filename
        assert path.is_file(), filename

        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )

        main_functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
        ]

        assert len(main_functions) == 1, (
            f"{filename} must define one main() function"
        )

        main_guards = [
            node
            for node in tree.body
            if _is_main_guard(node)
        ]

        assert len(main_guards) == 1, (
            f"{filename} must have one __main__ guard"
        )

        main_is_called = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "main"
            for node in ast.walk(main_guards[0])
        )

        assert main_is_called, (
            f"{filename} must call main() inside its __main__ guard"
        )
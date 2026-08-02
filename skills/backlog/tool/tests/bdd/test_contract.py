from __future__ import annotations

import ast
from pathlib import Path


BDD_ROOT = Path(__file__).parent


def test_bdd_suite_uses_real_database_without_test_doubles() -> None:
    forbidden_modules = {"mock", "unittest.mock", "pytest_mock"}
    forbidden_fixtures = {"monkeypatch", "mocker", "mock", "mocked"}
    forbidden_calls = {"patch", "Mock", "MagicMock", "create_autospec"}

    for path in sorted(BDD_ROOT.glob("*.py")):
        if path == Path(__file__):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not forbidden_modules.intersection(
                    alias.name for alias in node.names
                ), f"test doubles are forbidden in {path.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in forbidden_modules, (
                    f"test doubles are forbidden in {path.name}"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = {argument.arg for argument in node.args.args}
                assert not arguments.intersection(forbidden_fixtures), (
                    f"mocking fixtures are forbidden in {path.name}:{node.lineno}"
                )
            elif isinstance(node, ast.Call):
                function = node.func
                name = (
                    function.id
                    if isinstance(function, ast.Name)
                    else function.attr
                    if isinstance(function, ast.Attribute)
                    else ""
                )
                assert name not in forbidden_calls, (
                    f"test-double call {name} is forbidden in {path.name}:{node.lineno}"
                )
            elif isinstance(node, ast.Constant) and node.value == ":memory:":
                raise AssertionError(
                    f"in-memory databases are forbidden in {path.name}"
                )

    harness = (BDD_ROOT / "conftest.py").read_text(encoding="utf-8")
    assert '"BACKLOG_DB": "sqlite"' in harness
    assert 'world.run("init", "."' in harness

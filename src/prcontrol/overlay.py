"""Explicit selected-test overlays in disposable code worktrees.

Only named tests and explicitly named ancestor conftest.py files are copied.
Production package initializers are never copied. This is trusted-code test
setup, not an adversarial sandbox or atomic filesystem transaction.
"""
import hashlib
from pathlib import Path


def checked(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('relative path must stay inside repository')
    path = root / rel
    for candidate in [path, *path.parents]:
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError('symlink overlay paths are unsupported')
    if not path.resolve().is_relative_to(root):
        raise ValueError('overlay path escaped repository')
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plan_overlay(head, code, targets, fixture_files=()):
    """Build a byte plan; no mutations, no automatic fixture discovery."""
    names = set()
    for target in targets:
        rel = target.split('::', 1)[0]
        name = Path(rel).name
        if not (name.startswith('test_') or name.endswith('_test.py')) or not name.endswith('.py'):
            raise ValueError('targets must identify explicit Python test files')
        names.add(str(Path(rel)))
    for rel in fixture_files:
        if Path(rel).name != 'conftest.py':
            raise ValueError('only explicit conftest.py fixtures are supported')
        if not any(Path(rel).parent in Path(t.split('::',1)[0]).parents for t in targets):
            raise ValueError('fixture must be an ancestor of a selected test')
        names.add(str(Path(rel)))
    result=[]
    for rel in sorted(names):
        source=checked(head, rel);destination=checked(code, rel)
        if not source.is_file():
            raise ValueError('overlay source must be a file')
        if destination.exists() and not destination.is_file():
            raise ValueError('overlay destination must be absent or a file')
        data=source.read_bytes()
        result.append({'path':rel, 'head_sha256':digest(data),
                       'previous_sha256':digest(destination.read_bytes()) if destination.exists() else None,
                       'data':data})
    return result


def apply_overlay(head, code, plan):
    """Recheck all paths and contents before writing any planned file.

    This is local trusted-worktree checking, not an adversarial sandbox or an
    atomic filesystem transaction. A write failure requires discarding the tree.
    """
    verified=[]
    for row in plan:
        source=checked(head,row['path']);destination=checked(code,row['path'])
        if digest(source.read_bytes()) != row['head_sha256']:
            raise ValueError('head test changed after planning')
        previous=digest(destination.read_bytes()) if destination.exists() else None
        if previous != row['previous_sha256']:
            raise ValueError('selected-code file changed after planning')
        verified.append((row,destination))
    for row,destination in verified:
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(row['data'])
    return [{k:v for k,v in row.items() if k != 'data'} for row,_ in verified]


_CONFIG_NAMES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini",
                 "pyproject.toml", "tox.ini", "setup.cfg")


def check_overlay_context(head, code, targets, fixture_files=()):
    """Reject differing configs and implicit fixture changes before any write.

    Both runs use their selected-code tree as pytest root. Requiring identical
    candidate config files avoids silently comparing different pytest options.
    Head fixture changes must be named explicitly; absent fixtures count too.
    """
    ancestors = {Path(".")}
    for target in targets:
        rel = Path(target.split("::", 1)[0])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("relative path must stay inside repository")
        ancestors.update(rel.parents)
    explicit = {Path(p) for p in fixture_files}
    checked_files = []
    for parent in sorted(ancestors):
        for name in (*_CONFIG_NAMES, "conftest.py"):
            rel = parent / name
            left, right = checked(head, rel), checked(code, rel)
            for path in (left, right):
                if path.exists() and not path.is_file():
                    raise ValueError("context paths must be absent or files")
            a = left.read_bytes() if left.exists() else None
            b = right.read_bytes() if right.exists() else None
            if a != b and not (name == "conftest.py" and rel in explicit):
                raise ValueError("overlay context differs without explicit fixture approval: " + str(rel))
            if a is not None or b is not None:
                checked_files.append({"path": str(rel),
                                      "head_sha256": digest(a) if a is not None else None,
                                      "selected_sha256": digest(b) if b is not None else None})
    return checked_files

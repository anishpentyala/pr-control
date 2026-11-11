"""Explicit overlay-file integrity, separate from the tracked-only policy."""
import json
import os
from pathlib import Path

import pytest

from test_cli import run_cli


@pytest.mark.parametrize('repeats', [1, 2])
@pytest.mark.parametrize('mutation', [
    "Path(__file__).write_text(Path(__file__).read_text()+'\\n# changed')",
    "Path(__file__).unlink()",
    "Path(__file__).chmod(0o755)",
    "p=Path.cwd().parent/'head'/'test_fix.py';p.write_text(p.read_text()+'\\n# changed head input')",
])
def test_explicit_overlay_mutation_discards_evidence(make_repo, tmp_path, mutation, repeats):
    if 'chmod' in mutation and os.name != 'posix':
        pytest.skip('POSIX executable mode')
    test = ('import calc\nfrom pathlib import Path\ndef test_fix():\n'
            ' if calc.VALUE==0:\n  '+mutation+'\n assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output = tmp_path/'result.json'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   '--repeats', str(repeats), '--json-out', str(output))
    assert proc.returncode == 1, proc.stderr
    data = json.loads(output.read_text())
    assert data['require_clean_overlays'] is True
    assert data['require_clean_worktrees'] is False
    assert not data['suite_diff']['complete']
    assert 'overlay input changed' in data['collection_errors']['base']
    assert not data['summary'].get('fix_confirmed')
    assert 'head not run' in data['collection_errors']['head']
    assert not data['runs']['head']['attempts']
    assert len(data['runs']['base']['attempts']) == 1
    first = data['runs']['base']['attempts'][0]
    assert not first['outcomes'] and not first['imports']
    assert not first['source_verified'] and not first['failure_details']


@pytest.mark.parametrize('enabled', [False, True])
def test_explicit_fixture_overlay_is_guarded_when_untracked_on_base(make_repo, tmp_path, enabled):
    fixture = ('from pathlib import Path\nimport calc\n'
               'def pytest_runtest_setup(item):\n'
               ' if calc.VALUE==0:\n'
               '  Path(__file__).write_text(Path(__file__).read_text()+"\\n# changed fixture")\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1\n',
         'conftest.py': fixture})
    output = tmp_path/'result.json'
    flags = ['--require-clean-overlays'] if enabled else []
    proc = run_cli(repo, base, head, '--overlay-tests', '--overlay-fixture', 'conftest.py',
                   *flags, '--json-out', str(output))
    assert proc.returncode == (1 if enabled else 0), proc.stderr
    data = json.loads(output.read_text())
    assert bool(data['summary'].get('fix_confirmed')) is not enabled
    if enabled:
        assert 'overlay input changed' in data['collection_errors']['base']


@pytest.mark.parametrize('scratch', [False, True])
@pytest.mark.parametrize('tracked_policy', [False, True])
def test_clean_overlay_and_unrelated_untracked_scratch_are_allowed(make_repo, tmp_path, scratch, tracked_policy):
    body = "Path('scratch.txt').write_text('scratch')" if scratch else 'pass'
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py':
         'import calc\nfrom pathlib import Path\ndef test_fix():\n '+body+'\n assert calc.VALUE==1\n'})
    output = tmp_path/'result.json'
    md = tmp_path/'result.md'
    extra = ['--require-clean-worktrees'] if tracked_policy else []
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   *extra, '--json-out', str(output), '--md-out', str(md))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(output.read_text())['summary'] == {'fix_confirmed': 1}
    assert 'explicit overlay-input integrity policy' in md.read_text()


def test_overlay_policy_requires_overlay_before_worktrees(make_repo, monkeypatch, capsys):
    from prcontrol import cli
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'}, {'calc.py': 'VALUE=1\n'})
    def forbidden(*args):
        pytest.fail('worktree should not be created for invalid option combination')
    monkeypatch.setattr(cli, 'make_worktree', forbidden)
    assert cli.main([str(repo), '--base', base, '--head', head, '--require-clean-overlays']) == 1
    assert 'requires --overlay-tests' in capsys.readouterr().err


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('error', [PermissionError, OSError, ValueError])
def test_overlay_snapshot_failure_is_generic_and_discards_evidence(make_repo, tmp_path, monkeypatch, phase, error):
    from prcontrol import cli, core
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1\n'})
    original = core._overlay_snapshot
    calls = []
    def snapshot(inputs):
        calls.append(inputs)
        if len(calls) == (1 if phase == 'before' else 2):
            raise error('/private/secret/overlay-name.py')
        return original(inputs)
    monkeypatch.setattr(core, '_overlay_snapshot', snapshot)
    output = tmp_path/'result.json'
    assert cli.main([str(repo), '--base', base, '--head', head, '--overlay-tests',
        '--require-clean-overlays', '--json-out', str(output)]) == 1
    data = json.loads(output.read_text())
    assert 'snapshot unavailable '+phase in data['collection_errors']['base']
    assert '/private/secret' not in output.read_text()
    assert not data['runs']['base']['imports']
    assert not data['runs']['base']['attempts'][0]['outcomes']


@pytest.mark.skipif(os.name != 'posix', reason='POSIX symlinks')
def test_overlay_parent_symlink_is_not_followed(tmp_path):
    from prcontrol.core import _overlay_snapshot
    tree = tmp_path/'tree';tree.mkdir()
    outside = tmp_path/'private';outside.mkdir()
    (outside/'test.py').write_text('outside bytes')
    (tree/'tests').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        _overlay_snapshot(((tree, ('tests/test.py',)),))


def test_restored_mutation_is_outside_before_after_guarantee(make_repo, tmp_path):
    test = ('import calc\nfrom pathlib import Path\ndef test_fix():\n'
            ' p=Path(__file__);old=p.read_bytes();p.write_bytes(old+b"\\n# transient");p.write_bytes(old)\n'
            ' assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output = tmp_path/'result.json'
    assert run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   '--json-out', str(output)).returncode == 0
    assert json.loads(output.read_text())['summary'] == {'fix_confirmed': 1}


@pytest.mark.parametrize('repeats', [1, 2])
def test_head_overlay_deletion_retains_incomplete_report(make_repo, tmp_path, repeats):
    test = ('import calc\nfrom pathlib import Path\ndef test_fix():\n'
            ' if calc.VALUE==1:Path(__file__).unlink()\n assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output = tmp_path/'result.json'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   '--repeats', str(repeats), '--json-out', str(output))
    assert proc.returncode == 1, proc.stderr
    data = json.loads(output.read_text())
    assert 'overlay input changed' in data['collection_errors']['head']
    assert len(data['runs']['head']['attempts']) == 1
    assert not data['runs']['head']['attempts'][0]['outcomes']
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')


def test_tracked_only_policy_leaves_untracked_overlay_mutation_outside_scope(make_repo, tmp_path):
    test = ('import calc\nfrom pathlib import Path\ndef test_fix():\n'
            ' if calc.VALUE==0:Path(__file__).write_text(Path(__file__).read_text()+"\\n# changed")\n'
            ' assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output = tmp_path/'result.json'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-worktrees',
                   '--json-out', str(output))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(output.read_text())['summary'] == {'fix_confirmed': 1}


@pytest.mark.parametrize('repeats', [1, 2])
@pytest.mark.parametrize('side', ['base', 'head'])
@pytest.mark.parametrize('mutation', ['delete', 'write'])
def test_timeout_after_overlay_deletion_checks_integrity_and_retains_reports(make_repo, tmp_path, side, repeats, mutation):
    value = 0 if side == 'base' else 1
    change = 'Path(__file__).unlink()' if mutation == 'delete' else "p=Path(__file__);p.write_text(p.read_text()+'\\n# changed')"
    test = ('import calc,time\nfrom pathlib import Path\ndef test_fix():\n'
            f' if calc.VALUE=={value}:\n'
            f'  {change}\n  time.sleep(5)\n'
            ' assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output, md = tmp_path/'result.json', tmp_path/'result.md'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   '--timeout', '1', '--repeats', str(repeats),
                   '--json-out', str(output), '--md-out', str(md))
    assert proc.returncode == 1, proc.stderr
    data = json.loads(output.read_text())
    assert 'overlay input changed' in data['collection_errors'][side]
    assert 'timed out' in data['collection_errors'][side]
    assert 'overlay input changed' in md.read_text()
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')
    assert len(data['runs'][side]['attempts']) == 1
    assert not data['runs'][side]['attempts'][0]['outcomes']
    assert not data['runs'][side]['imports']
    if side == 'base':
        assert 'head not run' in data['collection_errors']['head']
        assert not data['runs']['head']['attempts']


def test_clean_timeout_preserves_timeout_and_does_not_invent_mutation(make_repo, tmp_path):
    test = ('import calc,time\ndef test_fix():\n'
            ' if calc.VALUE==0:time.sleep(5)\n assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    output = tmp_path/'result.json'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-overlays',
                   '--timeout', '1', '--repeats', '2', '--json-out', str(output))
    assert proc.returncode == 1, proc.stderr
    data = json.loads(output.read_text())
    assert 'timed out' in data['collection_errors']['base']
    assert 'overlay input' not in data['collection_errors']['base']
    assert len(data['runs']['base']['attempts']) == 2
    assert len(data['runs']['head']['attempts']) == 2
    assert not data['summary'].get('fix_confirmed')


@pytest.mark.parametrize('error', [PermissionError, ValueError])
def test_after_timeout_snapshot_failure_stops_repeats_and_preserves_reports(make_repo, tmp_path, monkeypatch, error):
    from prcontrol import cli, core
    test = ('import calc,time\ndef test_fix():\n'
            ' if calc.VALUE==0:time.sleep(5)\n assert calc.VALUE==1\n')
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': test})
    snapshot, calls = core._overlay_snapshot, []
    def failing_after(inputs):
        calls.append(inputs)
        if len(calls) == 2:
            raise error('/private/secret/path')
        return snapshot(inputs)
    monkeypatch.setattr(core, '_overlay_snapshot', failing_after)
    output, md = tmp_path/'result.json', tmp_path/'result.md'
    assert cli.main([str(repo), '--base', base, '--head', head,
        '--overlay-tests', '--require-clean-overlays', '--timeout', '1',
        '--repeats', '2', '--json-out', str(output), '--md-out', str(md)]) == 1
    data = json.loads(output.read_text())
    assert 'snapshot unavailable after' in data['collection_errors']['base']
    assert 'timed out' in data['collection_errors']['base']
    assert '/private/secret' not in output.read_text()+md.read_text()
    assert len(data['runs']['base']['attempts']) == 1
    assert not data['runs']['head']['attempts']
    assert not data['runs']['base']['imports']
    assert not data['suite_diff']['complete']

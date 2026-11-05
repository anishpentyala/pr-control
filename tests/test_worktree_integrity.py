"""Optional tracked-file integrity policy must catch self-modifying controls."""
import json
import pytest
from test_cli import run_cli


@pytest.mark.parametrize('side',['base','head'])
@pytest.mark.parametrize('repeats',[1,2])
def test_tracked_test_mutation_fails_optional_integrity_policy(make_repo,tmp_path,side,repeats):
    test='''import calc
from pathlib import Path
def test_fix():
 if calc.SIDE==SIDE:
  Path('calc.py').write_text(Path('calc.py').read_text()+'\\n# changed by test\\n')
 assert calc.VALUE==1
'''.replace('SIDE:',repr(side)+':')
    repo,base,head=make_repo({'calc.py':'VALUE=0\nSIDE="base"\n'},
                           {'calc.py':'VALUE=1\nSIDE="head"\n','test_fix.py':test})
    path=tmp_path/'report.json'
    proc=run_cli(repo,base,head,'--require-clean-worktrees','--repeats',str(repeats),'--json-out',str(path))
    assert path.exists(),proc.stderr
    data=json.loads(path.read_text())
    assert proc.returncode==1
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')
    assert 'tracked worktree changed' in data['collection_errors'][side]


@pytest.mark.parametrize('kind',['untracked','clean'])
def test_untracked_test_scratch_does_not_fail_integrity_policy(make_repo,tmp_path,kind):
    body="Path('.scratch').write_text('temporary')" if kind=='untracked' else 'pass'
    test='import calc\nfrom pathlib import Path\ndef test_fix():\n '+body+'\n assert calc.VALUE==1\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'}, {'calc.py':'VALUE=1\n','test_fix.py':test})
    path=tmp_path/'report.json'
    proc=run_cli(repo,base,head,'--require-clean-worktrees','--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    assert json.loads(path.read_text())['summary']=={'fix_confirmed':1}


@pytest.mark.parametrize('mutation',[
    "Path(__file__).write_text(Path(__file__).read_text()+'\\n# test changed')",
    "Path('calc.py').unlink()",
    "Path('calc.py').chmod(0o755)",
    "Path('new.py').write_text('X=1');subprocess.run(['git','add','new.py'],check=True)",
])
def test_source_test_mode_deletion_and_staged_changes_are_detected(make_repo,tmp_path,mutation):
    test='import calc,subprocess\nfrom pathlib import Path\ndef test_fix():\n '+mutation+'\n assert calc.VALUE==1\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'}, {'calc.py':'VALUE=1\n','test_fix.py':test})
    path=tmp_path/'report.json'
    proc=run_cli(repo,base,head,'--require-clean-worktrees','--json-out',str(path))
    assert proc.returncode==1,proc.stderr
    data=json.loads(path.read_text())
    assert not data['suite_diff']['complete']
    assert any('tracked worktree changed' in error for error in data['collection_errors'].values())


def test_default_does_not_enable_new_policy(make_repo,tmp_path):
    test="import calc\nfrom pathlib import Path\ndef test_fix():\n Path('calc.py').write_text('VALUE=1\\n')\n assert calc.VALUE==1\n"
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'}, {'calc.py':'VALUE=1\n','test_fix.py':test})
    path=tmp_path/'report.json'
    assert run_cli(repo,base,head,'--json-out',str(path)).returncode==0
    assert json.loads(path.read_text())['summary']=={'fix_confirmed':1}


def test_overlay_baseline_is_taken_after_authorized_test_copy(make_repo,tmp_path):
    test='import calc\ndef test_fix():assert calc.VALUE==1\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'}, {'calc.py':'VALUE=1\n','test_fix.py':test})
    path=tmp_path/'report.json';md=tmp_path/'report.md'
    proc=run_cli(repo,base,head,'--overlay-tests','--require-clean-worktrees',
                 '--json-out',str(path),'--md-out',str(md))
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['require_clean_worktrees'] is True
    assert data['summary']=={'fix_confirmed':1}
    assert 'tracked-file integrity policy' in md.read_text()


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('error_kind', ['permission', 'io', 'git'])
def test_snapshot_failure_writes_incomplete_report_without_exception_details(
        make_repo, tmp_path, monkeypatch, capsys, phase, error_kind):
    from prcontrol import core, cli
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'},
        {'calc.py': 'VALUE=1\n', 'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1\n'})
    original = core._tracked_snapshot
    calls = []
    secret = '/private/temp/tree/sensitive-filename.py'
    errors = {'permission': PermissionError(secret), 'io': OSError(secret),
              'git': core.PrControlError('git ls-files failed: '+secret)}
    def snapshot(tree):
        calls.append(tree)
        # Ordinary base attempt checks base+head before and after pytest.
        if len(calls) == (1 if phase == 'before' else 3):
            raise errors[error_kind]
        return original(tree)
    monkeypatch.setattr(core, '_tracked_snapshot', snapshot)
    path = tmp_path/'failure.json'
    md = tmp_path/'failure.md'
    assert cli.main([str(repo), '--base', base, '--head', head,
                     '--require-clean-worktrees', '--json-out', str(path),
                     '--md-out', str(md)]) == 1
    data = json.loads(path.read_text())
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')
    assert 'snapshot unavailable '+phase+' pytest' in data['collection_errors']['base']
    run = data['runs']['base']
    assert not run['source_verified'] and not run['imports'] and not run['collected_counts']
    assert all(not a['outcomes'] and not a['failure_details'] and a['test_source'] is None
               for a in run['attempts'])
    captured = capsys.readouterr()
    assert secret not in path.read_text()+md.read_text()+captured.out+captured.err
    assert len(calls) == (3 if phase == 'before' else 5)


def test_overlay_untracked_base_test_copy_is_outside_policy(make_repo, tmp_path):
    test = "import calc\nfrom pathlib import Path\ndef test_fix():\n if calc.VALUE==0:Path(__file__).write_text(Path(__file__).read_text()+'\\n# changed copy')\n assert calc.VALUE==1\n"
    repo, base, head = make_repo({'calc.py':'VALUE=0\n'},
        {'calc.py':'VALUE=1\n', 'test_fix.py':test})
    path = tmp_path/'report.json'
    proc = run_cli(repo, base, head, '--overlay-tests', '--require-clean-worktrees',
                   '--json-out', str(path))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(path.read_text())['summary'] == {'fix_confirmed':1}


@pytest.mark.skipif(__import__('os').name != 'posix', reason='POSIX file permissions')
def test_real_unreadable_tracked_file_after_pytest_is_reported(make_repo, tmp_path):
    import os
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        pytest.skip('root can read mode-000 files')
    secret = 'private-tracked-data.txt'
    test = "import calc\nfrom pathlib import Path\ndef test_fix():\n Path('"+secret+"').chmod(0)\n assert calc.VALUE==1\n"
    repo, base, head = make_repo({'calc.py':'VALUE=0\n',secret:'data'},
        {'calc.py':'VALUE=1\n', 'test_fix.py':test})
    path = tmp_path/'report.json'
    proc = run_cli(repo, base, head, '--require-clean-worktrees', '--json-out', str(path))
    assert path.exists(), proc.stderr
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')
    for side in ('base', 'head'):
        assert 'snapshot unavailable after pytest' in data['collection_errors'][side]
        assert not data['runs'][side]['imports']
    assert secret not in proc.stdout+proc.stderr+str(data['collection_errors'])

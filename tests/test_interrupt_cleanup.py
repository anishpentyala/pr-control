"""Controller cancellation must not leave its normal pytest session running."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from conftest import git
from prcontrol import core


def wait_for(path, proc=None):
    deadline = time.monotonic() + 15
    while not path.exists() and time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.02)
    assert path.exists(), 'synthetic pytest child never started'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX signal and process-group cleanup')
@pytest.mark.parametrize('exception', [KeyboardInterrupt, OSError])
def test_capture_exception_stops_pytest_and_reraises(make_repo, tmp_path, monkeypatch, exception):
    ready, survived = tmp_path / 'ready', tmp_path / 'survived'
    body = (f'import time\nfrom pathlib import Path\nimport sample\n'
            f'def test_sleep():\n Path({str(ready)!r}).write_text("ready")\n'
            f' time.sleep(1.5)\n Path({str(survived)!r}).write_text("survived")\n'
            ' assert sample.VALUE == 1\n')
    repo, _, head = make_repo({'sample.py': 'VALUE=0\n'},
                              {'sample.py': 'VALUE=1\n', 'test_sleep.py': body})
    worktree = core.make_worktree(repo, head, tmp_path / 'head')
    children = []
    popen = core.subprocess.Popen

    def record(*args, **kwargs):
        proc = popen(*args, **kwargs)
        children.append(proc)
        return proc

    def interrupt(proc, timeout):
        wait_for(ready, proc)
        raise exception('synthetic controller interruption')

    monkeypatch.setattr(core.subprocess, 'Popen', record)
    monkeypatch.setattr(core, '_capture_output', interrupt)
    try:
        with pytest.raises(exception, match='synthetic controller interruption'):
            core.run_pytest(worktree, worktree, 'head', ['test_sleep.py'], 30)
        time.sleep(1.7)
        assert not survived.exists(), 'pytest continued writing after controller exception'
        assert children[0].poll() is not None
    finally:
        for proc in children:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        core.cleanup(repo, [worktree])


@pytest.mark.skipif(os.name != 'posix', reason='POSIX signal and process-group cleanup')
def test_cli_sigint_stops_test_child_before_removing_worktrees(make_repo, tmp_path):
    ready, survived = tmp_path / 'ready', tmp_path / 'survived'
    # Record the pytest group so the fixture can clean up even on a broken baseline.
    group_file = tmp_path / 'group'
    body = (f'import time,os\nfrom pathlib import Path\nimport sample\n'
            f'def test_sleep():\n Path({str(group_file)!r}).write_text(str(os.getpgrp()))\n'
            f' Path({str(ready)!r}).write_text("ready")\n'
            f' time.sleep(1.5)\n Path({str(survived)!r}).write_text("survived")\n'
            ' assert sample.VALUE == 1\n')
    repo, base, head = make_repo({'sample.py': 'VALUE=0\n'},
                                {'sample.py': 'VALUE=1\n', 'test_sleep.py': body})
    proc = subprocess.Popen([sys.executable, '-m', 'prcontrol.cli', str(repo),
                             '--base', base, '--head', head],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(ready, proc)
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=15)
        time.sleep(1.7)
        assert proc.returncode != 0
        assert not survived.exists(), 'pytest survived SIGINT to the verifier'
        assert git(repo, 'worktree', 'list', '--porcelain').count('worktree ') == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        if group_file.exists():
            try:
                os.killpg(int(group_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_spawn_failure_preserves_original_error(tmp_path, monkeypatch):
    (tmp_path / 'test_one.py').write_text('def test_one(): pass\n')
    def fail(*args, **kwargs):
        raise OSError('synthetic spawn failure')
    monkeypatch.setattr(core.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='synthetic spawn failure'):
        core.run_pytest(tmp_path, tmp_path, 'head', ['test_one.py'], 30)

@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group identity')
@pytest.mark.parametrize('exception', [KeyboardInterrupt, OSError])
def test_already_reaped_child_never_supplies_a_group_to_signal(tmp_path, monkeypatch, exception):
    (tmp_path / 'test_one.py').write_text('def test_one(): pass\n')
    killed=[]
    def reaped_then_error(proc, timeout):
        proc.communicate(timeout=15)
        assert proc.returncode is not None
        raise exception('synthetic error after reap')
    monkeypatch.setattr(core, '_capture_output', reaped_then_error)
    monkeypatch.setattr(core.os, 'killpg', lambda pid,sig:killed.append((pid,sig)))
    with pytest.raises(exception, match='synthetic error after reap'):
        core.run_pytest(tmp_path,tmp_path,'head',['test_one.py'],30)
    assert killed == [], 'cleanup signaled a released process-group number'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX unreaped child ownership')
@pytest.mark.parametrize('exception', [KeyboardInterrupt, OSError])
def test_pipe_join_keeps_exited_child_unreaped_until_cleanup(tmp_path, monkeypatch, exception):
    (tmp_path / 'test_one.py').write_text('def test_one(): pass\n')
    children=[]
    popen=core.subprocess.Popen
    join=core.threading.Thread.join
    def record(*args,**kwargs):
        p=popen(*args,**kwargs);children.append(p);return p
    injected=[]
    def interrupted_join(thread,*args,**kwargs):
        if not injected:
            p=children[0]
            # This must be a zombie still owned by us, not a released PID.
            injected.append(p.returncode is None)
            raise exception('synthetic pipe join interruption')
        return join(thread,*args,**kwargs)
    monkeypatch.setattr(core.subprocess,'Popen',record)
    monkeypatch.setattr(core.threading.Thread,'join',interrupted_join)
    try:
        with pytest.raises(exception,match='synthetic pipe join interruption'):
            core.run_pytest(tmp_path,tmp_path,'head',['test_one.py'],30)
        assert injected == [True], 'child identity released before pipe cleanup'
        assert children[0].returncode is not None
    finally:
        for p in children:
            if p.returncode is None:p.kill();p.wait()

@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group identity')
def test_already_reaped_timeout_never_signals_released_group(tmp_path, monkeypatch):
    (tmp_path/'test_one.py').write_text('def test_one(): pass\n')
    killed=[]
    def reaped_then_timeout(proc,timeout):
        proc.communicate(timeout=15)
        raise subprocess.TimeoutExpired(proc.args,timeout)
    monkeypatch.setattr(core,'_capture_output',reaped_then_timeout)
    monkeypatch.setattr(core.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    result=core.run_pytest(tmp_path,tmp_path,'head',['test_one.py'],30)
    assert result.returncode==-1
    assert killed==[], 'timeout cleanup signaled a released process-group number'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX availability simulation')
def test_capture_works_without_waitid_on_supported_python(tmp_path, monkeypatch):
    (tmp_path/'sample.py').write_text('VALUE=1\n')
    (tmp_path/'test_one.py').write_text('import sample\ndef test_one():assert sample.VALUE==1\n')
    monkeypatch.delattr(core.os,'waitid',raising=False)
    try:
        result=core.run_pytest(tmp_path,tmp_path,'head',['test_one.py'],30)
    except AttributeError as error:
        pytest.fail('verification requires unavailable waitid: '+str(error))
    assert result.returncode==0 and result.collection_error is None
    assert result.source_verified

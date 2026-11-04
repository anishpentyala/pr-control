import pytest
from prcontrol.overlay import plan_overlay,apply_overlay

@pytest.fixture
def roots(tmp_path):
    h=tmp_path/'head';c=tmp_path/'code';h.mkdir();c.mkdir()
    for r,v in [(h,1),(c,0)]:
        (r/'src/pkg/tests').mkdir(parents=True)
        (r/'src/pkg/__init__.py').write_text(f'VALUE={v}\n')
        (r/'src/pkg/conftest.py').write_text(f'# fixture{v}\n')
        (r/'src/pkg/tests/test_value.py').write_text(f'# test{v}\n')
    return h,c


def test_only_named_tests_and_fixture_copied(roots):
    h,c=roots;before=(c/'src/pkg/__init__.py').read_bytes()
    p=plan_overlay(h,c,['src/pkg/tests/test_value.py::test_value'],['src/pkg/conftest.py'])
    manifest=apply_overlay(h,c,p)
    assert len(manifest)==2 and all('data' not in x for x in manifest)
    assert (c/'src/pkg/__init__.py').read_bytes()==before
    for x in manifest:assert (h/x['path']).read_bytes()==(c/x['path']).read_bytes()

@pytest.mark.parametrize('path',['src/pkg/__init__.py','../test_escape.py','/tmp/test_escape.py','src/pkg/file.py'])
def test_invalid_target_rejected(roots,path):
    with pytest.raises(ValueError):plan_overlay(*roots,[path])

@pytest.mark.parametrize('path',['src/pkg/__init__.py','conftest.txt','other/conftest.py'])
def test_invalid_fixture_rejected(roots,path):
    with pytest.raises(ValueError):plan_overlay(*roots,['src/pkg/tests/test_value.py'],[path])

@pytest.mark.parametrize('root_index',[0,1])
@pytest.mark.parametrize('ancestor',[False,True])
def test_symlinks_rejected(roots,tmp_path,root_index,ancestor):
    h,c=roots;r=roots[root_index];p=r/'src/pkg/tests/test_link.py'
    if ancestor:
        p=r/'src/pkg/link';p.symlink_to(tmp_path,target_is_directory=True)
        target='src/pkg/link/test_link.py'
    else:
        p.symlink_to(r/'src/pkg/tests/test_value.py');target='src/pkg/tests/test_link.py'
    with pytest.raises(ValueError):plan_overlay(h,c,[target])

@pytest.mark.parametrize('which',[0,1])
def test_changed_files_abort_before_any_write(roots,which):
    h,c=roots;p=plan_overlay(h,c,['src/pkg/tests/test_value.py'],['src/pkg/conftest.py'])
    untouched=(c/'src/pkg/conftest.py').read_bytes()
    (roots[which]/'src/pkg/tests/test_value.py').write_text('changed')
    with pytest.raises(ValueError):apply_overlay(h,c,p)
    assert (c/'src/pkg/conftest.py').read_bytes()==untouched


def test_missing_new_test_allowed_and_deduplicated(roots):
    h,c=roots;(c/'src/pkg/tests/test_value.py').unlink()
    p=plan_overlay(h,c,['src/pkg/tests/test_value.py::a','src/pkg/tests/test_value.py::b'])
    assert len(p)==1 and p[0]['previous_sha256'] is None
    apply_overlay(h,c,p)
    assert (c/'src/pkg/tests/test_value.py').read_bytes()==(h/'src/pkg/tests/test_value.py').read_bytes()


def test_new_package_initializer_is_not_implicitly_copied(roots):
    h,c=roots
    (h/'src/pkg/tests/__init__.py').write_text('HEAD_ONLY=True\n')
    manifest=apply_overlay(h,c,plan_overlay(h,c,['src/pkg/tests/test_value.py']))
    assert not (c/'src/pkg/tests/__init__.py').exists()
    assert [x['path'] for x in manifest]==['src/pkg/tests/test_value.py']


def test_no_fixture_discovery_or_production_sibling_copy(roots):
    h,c=roots
    (h/'src/pkg/extra.py').write_text('HEAD_ONLY=True\n')
    before=(c/'src/pkg/conftest.py').read_bytes()
    manifest=apply_overlay(h,c,plan_overlay(h,c,['src/pkg/tests/test_value.py']))
    assert (c/'src/pkg/conftest.py').read_bytes()==before
    assert not (c/'src/pkg/extra.py').exists()
    assert len(manifest)==1


def test_source_missing_leaves_destination_unchanged(roots):
    h,c=roots;before=(c/'src/pkg/tests/test_value.py').read_bytes()
    with pytest.raises(ValueError):
        plan_overlay(h,c,['src/pkg/tests/test_value.py','src/pkg/tests/test_missing.py'])
    assert (c/'src/pkg/tests/test_value.py').read_bytes()==before


def test_destination_directory_rejected(roots):
    h,c=roots;p=c/'src/pkg/tests/test_value.py';p.unlink();p.mkdir()
    with pytest.raises(ValueError):plan_overlay(h,c,['src/pkg/tests/test_value.py'])


def test_symlink_introduced_after_plan_aborts_all_writes(roots):
    h,c=roots;p=plan_overlay(h,c,['src/pkg/tests/test_value.py'],['src/pkg/conftest.py'])
    target=c/'src/pkg/tests/test_value.py';before=(c/'src/pkg/conftest.py').read_bytes()
    target.unlink();target.symlink_to(h/'src/pkg/tests/test_value.py')
    with pytest.raises(ValueError):apply_overlay(h,c,p)
    assert (c/'src/pkg/conftest.py').read_bytes()==before


def test_source_change_after_plan_aborts_even_when_bytes_in_plan_still_match(roots):
    h,c=roots;p=plan_overlay(h,c,['src/pkg/tests/test_value.py'])
    original=(c/'src/pkg/tests/test_value.py').read_bytes()
    (h/'src/pkg/tests/test_value.py').write_text('new head test\n')
    with pytest.raises(ValueError):apply_overlay(h,c,p)
    assert (c/'src/pkg/tests/test_value.py').read_bytes()==original


@pytest.mark.parametrize('rel',['pytest.ini','tests/pytest.ini','tests/conftest.py'])
def test_context_difference_rejected_without_write(roots,rel):
    from prcontrol.overlay import check_overlay_context
    h,c=roots;(h/rel).parent.mkdir(parents=True,exist_ok=True);(h/rel).write_text('# head\n')
    target='tests/test_context.py'
    with pytest.raises(ValueError):check_overlay_context(h,c,[target])


def test_root_fixture_explicit_allowance_is_recorded(roots):
    from prcontrol.overlay import check_overlay_context
    h,c=roots;(h/'conftest.py').write_text('# head\n');(c/'src/pkg/conftest.py').write_bytes((h/'src/pkg/conftest.py').read_bytes())
    rows=check_overlay_context(h,c,['src/pkg/tests/test_value.py'],['conftest.py'])
    f=next(r for r in rows if r['path']=='conftest.py')
    assert f['head_sha256'] and f['selected_sha256'] is None

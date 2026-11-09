"""Quoted diagnostics must retain text without becoming report structure."""
import json
import os
import subprocess
import sys

import pytest

from prcontrol.report import _code_block, render_markdown


@pytest.mark.parametrize('text', [
    'ordinary diagnostic',
    'literal ``` marker',
    '```\n## diagnostic heading\n```',
    '``````\n## diagnostic heading\n``````',
    '~~~\n## diagnostic heading\n~~~',
    '   ```\n# indented closing fence\n```',
    '<script>not HTML</script>\n```\n**not emphasis**',
    'prefix\n'+'`'*100+'\nsuffix',
    'line1\r\nline2\t`\n',
    '',
])
def test_adaptive_code_block_preserves_literal_text(text):
    opening, quoted, closing = _code_block(text)
    assert quoted == text
    assert opening == closing + 'text'
    assert set(closing) == {'`'} and len(closing) >= 3
    assert max((len(part) for part in __import__('re').findall(r'`+', text)), default=0) < len(closing)


def payload(traceback, error):
    return {'repo':'example','base':'a'*40,'head':'b'*40,
            'env':{'python':'3.13','pytest':'test','platform':'test'},
            'verdicts':[{'test':'test_x','base':'failed','head':'passed','verdict':'fix_confirmed',
                         'base_failure_details':[{'phase':'call','classification':'failed','traceback':traceback}]}],
            'collection_errors':{'base':error},
            'suite_diff':{'complete':False,'only_on_base':[],'only_on_head':[],
                          'base_failed':['test_x'],'head_failed':[],
                          'new_failures_on_head':[],'fixed_on_head':['test_x']},
            'summary':{'inconclusive':1}}


@pytest.mark.parametrize('width',[3,4,7])
def test_both_failure_and_incomplete_sections_use_literal_containing_fences(width):
    text='literal ``` marker\n'+'`'*width+'\n## diagnostic heading\n'+'`'*width
    report=render_markdown(payload(text,text))
    opening,body,closing=_code_block(text)
    assert report.count('\n'+opening+'\n'+text+'\n'+closing+'\n')==2
    assert 'literal ~~~ marker' not in report
    assert report.endswith('- transition_with_skips: 0\n')


@pytest.mark.parametrize('width',[3,5])
@pytest.mark.parametrize('mode',['missing-report','assertion'])
def test_real_cli_preserves_json_verdict_and_markdown_diagnostics(make_repo,tmp_path,width,mode):
    message='literal ``` marker\n'+'`'*width+'\n## diagnostic heading\n'+'`'*width+'\n'
    if mode=='missing-report':
        body='import calc,os\ndef test_fix():\n os.write(2,'+repr(message.encode())+')\n os._exit(3)\n'
    else:
        body='import calc\ndef test_fix():\n assert calc.VALUE==1,'+repr(message)+'\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0\n','test_fix.py':body}, {'calc.py':'VALUE=1\n'})
    md=tmp_path/'report.md';js=tmp_path/'report.json'
    env=dict(os.environ,PYTEST_ADDOPTS='-s',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result=subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
                           '--targets','test_fix.py','--json-out',str(js),'--md-out',str(md)],
                          capture_output=True,text=True,env=env)
    data=json.loads(js.read_text());report=md.read_text()
    if mode=='missing-report':
        assert result.returncode==1 and not data['suite_diff']['complete']
        assert data['summary']=={}
        texts=list(data['collection_errors'].values())
    else:
        assert result.returncode==0 and data['suite_diff']['complete']
        assert data['summary']=={'fix_confirmed':1}
        texts=[data['verdicts'][0]['base_failure_details'][0]['traceback']]
    assert texts
    for text in texts:
        assert 'literal ``` marker' in text
        opening,body,closing=_code_block(text)
        assert '\n'+opening+'\n'+text+'\n'+closing+'\n' in report
    assert 'literal ~~~ marker' not in report

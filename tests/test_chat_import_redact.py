from scripts.chat_import.redact import redact

def test_redacts_credentials_without_changing_ordinary_source():
    keys=['sk-proj-'+'a'*32,'xsmtpsib-'+'b'*50,'xkeysib-'+'c'*50,'eyJ'+'d'*90]
    raw='keep this\n'+'\n'.join(keys)+'\nSMTP key: '+('e'*40)
    safe, counts=redact(raw)
    assert all(k not in safe for k in keys)
    assert 'e'*40 not in safe and safe.startswith('keep this\n')
    assert sum(counts.values())==5
    assert redact(safe)==(safe,{})

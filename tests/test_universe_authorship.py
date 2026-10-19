"""The personal view must distinguish user notes without altering source records."""
from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app


def test_scoped_authorship_metadata_retains_imported_sources(tmp_path):
    client = TestClient(create_app(str(tmp_path / 'db'), str(tmp_path / 'raw'), str(tmp_path / 'art')))
    for author in ('user', 'mcp-agent:history-import'):
        result = client.post('/api/notes', json={'title': author, 'content': 'original', 'author': author})
        assert result.status_code == 201
    foreign = client.post('/api/notes', json={'title': 'private', 'content': 'private', 'scope': 'private'}).json()
    data = client.get('/api/universe', params={'topic_id': 'all', 'kind': 'note'}).json()
    assert {item['author'] for item in data['items']} == {'user', 'mcp-agent:history-import'}
    assert foreign['id'] not in {item['id'] for item in data['items']}
    assert data['total'] == 2
    assert all(client.get('/api/notes/' + item['id']).json()['content'] == 'original' for item in data['items'])

from fastapi.testclient import TestClient
from backend.adapters.api.app import create_app


def test_module_application_initialization_is_separate_from_daily_user_data():
    from pathlib import Path
    from backend.adapters.api.app import _data_root
    assert _data_root.resolve() != (Path(__file__).resolve().parents[1] / 'data').resolve()


def test_new_contracts_are_wired_to_main_application_with_real_persistence(tmp_path):
    app = create_app(str(tmp_path/'db'), str(tmp_path/'raw'), str(tmp_path/'art'))
    with TestClient(app) as client:
        assert client.get('/api/schema/contracts').status_code == 200
        assert len(client.get('/api/schema/contracts').json()['types']) == 20
        response = client.post('/api/token-budget/reservations', json={'request_key': 'first', 'tokens': 100})
        assert response.status_code == 201, response.text
        rid = response.json()['id']
        assert client.post(f'/api/token-budget/reservations/{rid}/settle', json={'tokens': 80, 'counting_mode': 'estimated'}).status_code == 200
        assert client.get('/api/token-budget').json()['used'] == 80
        assert client.get('/api/health').json()['remaining_token_budget'] == 99_920
        report = client.get('/api/maintenance/health').json()
        assert report['metrics']['accuracy'] is None
        assert client.get('/api/lineage/sessions').json()['items'] == []
        assert client.post('/api/projections/knowledge/rebuild').status_code == 200
        assert client.get('/api/projections/knowledge').json()['nodes'] == []
        response = client.put('/api/view-state', json={'workspace_id':'test','expected_revision':0,'state':{}})
        assert response.status_code == 200, response.text
        assert client.put('/api/view-state', json={'workspace_id':'test','expected_revision':0,'state':{}}).status_code == 409
        assert client.get('/api/evolution/records', params={'scope':'default'}).json() == []
        assert client.get('/api/intake/support').status_code == 200
        metrics = client.get('/api/telemetry/cache-metrics').json()
        assert metrics['provider'] == {'reports': 0, 'cached_tokens': None}
        assert 'retrieval' in metrics and 'context' in metrics


def test_search_accepts_explicit_consistency_and_historical_contract(tmp_path):
    app = create_app(str(tmp_path/'db'), str(tmp_path/'raw'), str(tmp_path/'art'))
    with TestClient(app) as client:
        response = client.post('/api/retrieval/search', json={'query':'example','read_mode':'historical_as_of',
            'as_of':'2000-01-01T00:00:00Z','recorded_as_of':'2000-01-01T00:00:00Z'})
        assert response.status_code == 200, response.text
        assert 'historical' in str(response.json())
        assert response.json()['retrieval']['consistency']['mode'] == 'historical_as_of'
        bad = client.post('/api/retrieval/search', json={'query':'example','read_mode':'invalid'})
        assert bad.status_code == 422
        bad_depth = client.post('/api/retrieval/search', json={'query':'example','max_depth':4})
        assert bad_depth.status_code == 422

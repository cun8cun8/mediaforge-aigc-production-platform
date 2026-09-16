"""Run outside the checkout, against the installed production wheel in Docker."""

import json
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

import mediaforge_p1
from fastapi.testclient import TestClient
from mediaforge_p1.api import create_app
from mediaforge_p1.service import MediaForgeService


def main():
    assert 'site-packages' in str(Path(mediaforge_p1.__file__))
    resources = files('mediaforge_p1')
    assert 'activePlanningRun' in resources.joinpath('static/app.js').read_text(encoding='utf-8')
    assert resources.joinpath('sql/vector-memory.sql').is_file()
    with TemporaryDirectory(prefix='planning-wheel-') as folder:
        root = Path(folder)
        app = create_app(output_root=root)
        with TestClient(app) as client:
            assert client.get('/planning/status').json()['configured'] is True
            assert client.get('/').status_code == 200
            response = client.post('/projects', json={
                'project_id': 'wheel_check', 'title': 'Midnight call',
                'premise': 'A mysterious call arrives from the future.', 'genre': 'suspense',
                'style': 'cinema', 'characters': ['Alice', 'Bob'], 'duration_seconds': 30, 'budget': 2,
            })
            assert response.status_code == 201, response.text
            response = client.post('/projects/wheel_check/planning')
            assert response.status_code == 200, response.text
            run = response.json()
            assert run['status'] == 'AWAITING_REVIEW' and len(run['specs']) == 6
            assert client.get('/projects/wheel_check').json()['shots'] == []
        restored = MediaForgeService(root)
        approved = restored.planning.review('wheel_check', run['run_id'], 'approve', 'verified', 'reviewer')
        assert approved['status'] == 'APPROVED'
        shot = restored.submit_shot('wheel_check', run['shots'][0]['shot_id'])
        assert shot['artifact'] and shot['quality']['passed']
        again = MediaForgeService(root)
        assert again.project_view('wheel_check')['story_bible']['planning_run_id'] == run['run_id']
        print(json.dumps({'passed': True, 'routes': len(app.routes), 'installed_package': mediaforge_p1.__file__,
                          'checks': ['packaged assets', 'graph draft', 'restart recovery', 'approval', 'media generation']}))


if __name__ == '__main__':
    main()

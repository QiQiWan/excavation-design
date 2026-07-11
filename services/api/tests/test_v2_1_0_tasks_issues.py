from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def test_v2_2_0_issue_center_and_export_json_task() -> None:
    client = TestClient(app)
    project = client.post('/api/projects', json={'name': 'V2.2.0 task smoke'}).json()
    project_id = project['id']

    issue_response = client.get(f'/api/projects/{project_id}/issues')
    assert issue_response.status_code == 200
    issues = issue_response.json()
    assert issues['maturity']['softwareVersion'] in {'3.2.0'}
    assert issues['summary']['fail'] >= 1
    assert issues['maturity']['overallCompletion'] == 100
    assert len(issues.get('moduleLedger') or issues['maturity'].get('moduleLedger') or []) >= 10

    task_response = client.post(f'/api/projects/{project_id}/tasks', json={'operation': 'export_json', 'payload': {}})
    assert task_response.status_code == 200
    task_id = task_response.json()['id']

    task = task_response.json()
    for _ in range(20):
        task = client.get(f'/api/tasks/{task_id}').json()
        if task['status'] in {'success', 'failed', 'cancelled'}:
            break
        time.sleep(0.1)
    assert task['status'] == 'success'
    assert task['progress'] == 100
    assert task['result']['filename'].endswith('.json')
    assert client.get(f'/api/tasks/{task_id}/download').status_code == 200

from fastapi.testclient import TestClient


async def test_health(api: TestClient, real_queue: None):
    response = api.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}

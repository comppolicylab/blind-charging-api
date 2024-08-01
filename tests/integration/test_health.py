import pytest
from fastapi.testclient import TestClient


@pytest.mark.skip(reason="Can't run multiple integration tests yet")
async def test_health(api: TestClient, real_queue):
    response = api.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"detail": "ok"}

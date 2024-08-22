import pytest
from fastapi.testclient import TestClient

PROTECTED_ROUTES = [
    ("POST", "/api/v1/exposure"),
    ("POST", "/api/v1/outcome"),
    ("POST", "/api/v1/redact"),
    ("GET", "/api/v1/redact/a/b"),
    ("GET", "/api/v1/blindreview/a/b"),
]

UNPROTECTED_ROUTES = [
    ("GET", "/api/v1/health"),
]

## PRESHARED SECRET


@pytest.mark.parametrize(
    "authn_config", ["method = 'preshared'\nsecret = 'something'"], indirect=True
)
async def test_authn_preshared_invalid(api: TestClient):
    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": "Bearer invalid"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    "authn_config", ["method = 'preshared'\nsecret = 'something'"], indirect=True
)
async def test_authn_preshared_valid(api: TestClient):
    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": "Bearer something"}
        )
        # As long as we don't get 401 or 403 we should be good.
        assert response.status_code in {200, 422}


@pytest.mark.parametrize(
    "authn_config", ["method = 'preshared'\nsecret = 'something'"], indirect=True
)
async def test_authn_preshared_corrupted(api: TestClient):
    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": "not even the right format"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    "authn_config", ["method = 'preshared'\nsecret = 'something'"], indirect=True
)
async def test_authn_preshared_missing(api: TestClient):
    for method, route in PROTECTED_ROUTES:
        response = api.request(method, route)
        assert response.status_code == 401


@pytest.mark.parametrize(
    "authn_config", ["method = 'preshared'\nsecret = 'something'"], indirect=True
)
async def test_authn_preshared_unprotected(api: TestClient):
    for method, route in UNPROTECTED_ROUTES:
        response = api.request(method, route)
        assert response.status_code == 200


## NO AUTHN


@pytest.mark.parametrize("authn_config", ["method = 'none'"], indirect=True)
async def test_authn_none(api: TestClient):
    for method, route in PROTECTED_ROUTES + UNPROTECTED_ROUTES:
        response = api.request(method, route)
        # As long as we don't get 401 or 403 we should be good.
        assert response.status_code in {200, 422}

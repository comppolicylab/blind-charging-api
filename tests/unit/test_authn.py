import asyncio
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.server.db import Revocation

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
    "authn_config", ['method = "preshared"\nsecret = ["s1", "s2"]'], indirect=True
)
async def test_authn_preshared_valid_multi(api: TestClient):
    for secret in ["s1", "s2"]:
        for method, route in PROTECTED_ROUTES:
            response = api.request(
                method, route, headers={"Authorization": f"Bearer {secret}"}
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


## CLIENT CREDENTIALS


@pytest.mark.parametrize(
    "authn_config",
    ["method = 'client_credentials'\nsecret = 'something'"],
    indirect=True,
)
async def test_authn_client_credentials_invalid(api: TestClient):
    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": "Bearer invalid"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    "authn_config",
    [
        """\
method = 'client_credentials'
secret = 'something'
[authentication.store]
engine = 'sqlite'
path = "{db_path}"
"""
    ],
    indirect=True,
)
async def test_authn_client_credentials_valid(api: TestClient, config, authn_db, now):
    async with authn_db.async_session() as sesh:
        client = await config.authentication.driver.register_client(sesh, "test")
        await sesh.commit()

    token_response = api.request(
        "POST",
        "/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )

    assert token_response.status_code == 200
    token_response_data = token_response.json()
    assert "access_token" in token_response_data
    assert token_response_data["token_type"] == "Bearer"
    assert token_response_data["expires_in"] == 86400

    token = token_response_data["access_token"]

    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": f"Bearer {token}"}
        )
        # As long as we don't get 401 or 403 we should be good.
        try:
            assert response.status_code in {200, 422}
        except AssertionError:
            print("Response:")
            print("STATUS CODE:", response.status_code)
            print("JSON:")
            print(response.json())
            raise

    # Expiration
    api.app.state.now = lambda: now() + timedelta(days=2)

    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            assert response.status_code == 401
        except AssertionError:
            print("Response:")
            print("STATUS CODE:", response.status_code)
            print("JSON:")
            print(response.json())
            raise


@pytest.mark.parametrize(
    "authn_config",
    [
        """\
method = 'client_credentials'
secret = 'something'
[authentication.store]
engine = 'sqlite'
path = "{db_path}"
"""
    ],
    indirect=True,
)
async def test_authn_client_credentials_valid_revoke(api: TestClient, config, authn_db):
    async with authn_db.async_session() as sesh:
        client = await config.authentication.driver.register_client(sesh, "test")
        await sesh.commit()

    token_response = api.request(
        "POST",
        "/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )

    assert token_response.status_code == 200
    token_response_data = token_response.json()
    assert "access_token" in token_response_data
    assert token_response_data["token_type"] == "Bearer"
    assert token_response_data["expires_in"] == 86400

    token = token_response_data["access_token"]

    revoke_response = api.request(
        "POST",
        "/api/v1/oauth2/revoke",
        json={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "token": token,
        },
    )
    assert revoke_response.status_code == 200

    for method, route in PROTECTED_ROUTES:
        response = api.request(
            method, route, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            assert response.status_code == 401
        except AssertionError:
            print("Response:")
            print("STATUS CODE:", response.status_code)
            print("JSON:")
            print(response.json())
            raise


@pytest.mark.parametrize(
    "authn_config",
    [
        """\
method = 'client_credentials'
secret = 'something'
[authentication.store]
engine = 'sqlite'
path = "{db_path}"
"""
    ],
    indirect=True,
)
async def test_authn_client_credentials_revoke_cleanup(
    api: TestClient, config, authn_db, now
):
    async with authn_db.async_session() as sesh:
        client = await config.authentication.driver.register_client(sesh, "test")
        await sesh.commit()

    token_response = api.request(
        "POST",
        "/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )

    token = token_response.json()["access_token"]

    api.request(
        "POST",
        "/api/v1/oauth2/revoke",
        json={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "token": token,
        },
    )

    token_id = jwt.decode(
        token, algorithms=["HS256"], options={"verify_signature": False}
    )["jti"]

    async with authn_db.async_session() as sesh:
        assert await Revocation.check(sesh, token_id) is True
        await sesh.commit()

    api.app.state.now = lambda: now() + timedelta(seconds=2 * 86400)
    new_token_response = api.request(
        "POST",
        "/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )
    new_token = new_token_response.json()["access_token"]

    api.request(
        "POST",
        "/api/v1/oauth2/revoke",
        json={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "token": new_token,
        },
    )

    await asyncio.sleep(0.1)
    async with authn_db.async_session() as sesh:
        assert await Revocation.check(sesh, token_id) is False
        await sesh.commit()

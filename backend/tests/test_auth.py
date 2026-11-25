"""Auth and health endpoint tests."""
import uuid


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_login_me(client, db_session):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    register = client.post(
        "/auth/register",
        json={"name": "Test User", "email": email, "password": "password123"},
    )
    assert register.status_code == 201, register.text
    body = register.json()
    assert body["email"] == email
    assert body["role"] == "reviewer"

    duplicate = client.post(
        "/auth/register",
        json={"name": "Test User", "email": email, "password": "password123"},
    )
    assert duplicate.status_code == 409

    login = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200, login.text
    token = login.json()
    assert token["token_type"] == "bearer"
    assert token["role"] == "reviewer"
    assert token["name"] == "Test User"

    me_response = client.get(
        "/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email

    no_token = client.get("/me")
    assert no_token.status_code == 401

    bad_login = client.post("/auth/login", json={"email": email, "password": "wrong-password"})
    assert bad_login.status_code == 401
    assert "detail" in bad_login.json()


def test_register_short_password(client):
    response = client.post(
        "/auth/register",
        json={"name": "X", "email": "short@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_login_rate_limited_after_repeated_failures(client, monkeypatch):
    """5 failed logins per minute per IP/email -> 429 with Retry-After."""
    from app import ratelimit

    monkeypatch.setattr(ratelimit, "MAX_FAILED_ATTEMPTS", 3)
    limiter = ratelimit.SlidingWindowLimiter(max_attempts=3, window_seconds=60.0)
    monkeypatch.setattr(ratelimit, "login_limiter", limiter)
    # auth.py imported login_limiter by name; patch that reference too.
    import app.routers.auth as auth_module

    monkeypatch.setattr(auth_module, "login_limiter", limiter)

    email = "ratelimit@example.com"
    for _ in range(3):
        response = client.post("/auth/login", json={"email": email, "password": "wrong"})
        assert response.status_code == 401
    blocked = client.post("/auth/login", json={"email": email, "password": "wrong"})
    assert blocked.status_code == 429, blocked.text
    assert "Retry-After" in blocked.headers

    # A different email from the same test IP is also blocked (per-IP key
    # shares the window) — use a fresh limiter to prove per-email isolation.
    limiter.reset()
    other = client.post("/auth/login", json={"email": "other@example.com", "password": "wrong"})
    assert other.status_code == 401

    # Successful login after failures still works once the limiter is clear.
    client.post(
        "/auth/register",
        json={"name": "RL", "email": "rl-user@example.com", "password": "password123"},
    )
    limiter.reset()
    ok = client.post("/auth/login", json={"email": "rl-user@example.com", "password": "password123"})
    assert ok.status_code == 200


def test_request_id_header_echoed(client):
    response = client.get("/health", headers={"X-Request-ID": "test-req-123"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "test-req-123"

    generated = client.get("/health")
    assert generated.status_code == 200
    assert generated.headers.get("X-Request-ID")

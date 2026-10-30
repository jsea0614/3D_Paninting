"""기기 로그 API의 저장, 입력 제한, 파일 회전을 검증합니다."""
import pytest
from fastapi.testclient import TestClient
from main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path))


def batch(**changes):
    value = {"device": "quest-1a2b3c4d", "platform": "Android", "role": "Client",
             "appVersion": "0.1.0", "clientId": 3, "characterId": 7, "dropped": 0,
             "entries": [{"t": "2026-10-28T14:03:11Z", "level": "Error", "msg": "오류",
                          "stack": "line1\nline2", "count": 4}]}
    return {**value, **changes}


def test_append(client, tmp_path):
    response = client.post("/api/logs", json=batch(dropped=2))
    assert response.status_code == 200
    assert response.json() == {"ok": True, "written": 1}
    text = (tmp_path / "logs/quest-1a2b3c4d.log").read_text(encoding="utf-8")
    assert "2026-10-28T14:03:11Z [Error] (client 3, char 7, Client 0.1.0) 오류 ×4" in text
    assert "\n    line1\n    line2\n" in text
    assert "[Warning] (dropped 2)" in text


@pytest.mark.parametrize("device", ["../x", "Quest-1", "quest-1\n", None, 123])
def test_invalid_device(client, device):
    assert client.post("/api/logs", json=batch(device=device)).status_code == 400


def test_body_limit(client):
    assert client.post("/api/logs", content=b"x" * (256 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize("entries", [[{}] * 201, {}, None, [1]])
def test_invalid_entries(client, entries):
    assert client.post("/api/logs", json=batch(entries=entries)).status_code == 400


def test_rotation(client, tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir(exist_ok=True)
    path = folder / "quest-1a2b3c4d.log"
    previous = b"x" * (5 * 1024 * 1024)
    path.write_bytes(previous)
    path.with_suffix(".log.1").write_bytes(b"older")
    assert client.post("/api/logs", json=batch()).status_code == 200
    assert path.with_suffix(".log.1").read_bytes() == previous
    assert "오류" in path.read_text(encoding="utf-8")


def test_list(client, tmp_path):
    assert client.get("/api/logs").json() == {"devices": []}
    client.post("/api/logs", json=batch())
    (tmp_path / "logs/quest-1a2b3c4d.log.1").write_text("old")
    devices = client.get("/api/logs").json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device"] == "quest-1a2b3c4d"
    assert devices[0]["size"] > 0
    assert devices[0]["updatedAt"].endswith("+00:00")


def test_sanitize(client, tmp_path):
    entries = [{"level": "unknown", "msg": "a" * 2100, "stack": "b" * 4100, "count": -4}]
    assert client.post("/api/logs", json=batch(entries=entries)).status_code == 200
    text = (tmp_path / "logs/quest-1a2b3c4d.log").read_text(encoding="utf-8")
    assert "[Log]" in text and "×" not in text
    assert "a" * 2000 in text and "a" * 2001 not in text
    assert "b" * 4000 in text and "b" * 4001 not in text


@pytest.mark.parametrize("content", [b"{", b"[]", b"null"])
def test_invalid_json(client, content):
    assert client.post("/api/logs", content=content).status_code == 400

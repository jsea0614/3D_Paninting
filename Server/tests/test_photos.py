"""손 카메라 사진 업로드 API를 검증합니다."""
import pytest
from fastapi.testclient import TestClient
from main import create_app

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path))


def post(client, data=JPEG, device="quest-1a2b3c4d", character_id="7"):
    return client.post("/api/photos", data={"device": device, "characterId": character_id},
                       files={"file": ("photo.jpg", data, "image/jpeg")})


def test_save_and_list(client, tmp_path):
    first = post(client)
    second = post(client)
    assert first.status_code == 200 and second.status_code == 200
    names = {first.json()["file"], second.json()["file"]}
    assert len(names) == 2 and all(n.startswith("07번_") and n.endswith(".jpg") for n in names)
    assert (tmp_path / "photos" / first.json()["file"]).read_bytes() == JPEG
    assert set(client.get("/api/photos").json()["photos"]) == names


def test_no_character(client):
    assert post(client, character_id="-1").json()["file"].startswith("번호없음_")


@pytest.mark.parametrize("device", ["../x", "Quest-1", "quest-1\n"])
def test_bad_device(client, device):
    assert post(client, device=device).status_code == 400


def test_not_jpeg(client):
    assert post(client, data=b"\x89PNG\r\n\x1a\n").status_code == 400


def test_too_big(client):
    assert post(client, data=JPEG + b"0" * (4 * 1024 * 1024)).status_code == 413

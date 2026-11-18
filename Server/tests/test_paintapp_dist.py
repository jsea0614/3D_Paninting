"""색칠 앱 배포 zip: 번호·서버 주소가 config.json에 들어가고, PC별 파일은 빠지고, 빌드가 바뀌면 다시 만든다."""
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from main import create_app


@pytest.fixture
def build(tmp_path):
    folder = tmp_path / "PaintApp"
    (folder / "PaintApp_Data").mkdir(parents=True)
    (folder / "PaintApp.exe").write_bytes(b"exe")
    (folder / "PaintApp_Data" / "level0").write_bytes(b"data")
    (folder / "config.json").write_text('{"serverUrl": "http://old", "studentId": "01", "backupFolder": "B"}', encoding="utf-8")
    (folder / "Backups").mkdir()
    (folder / "Backups" / "x.png").write_bytes(b"png")
    return folder


def client_for(tmp_path, build):
    return TestClient(create_app(tmp_path / "data", paintapp_dir=build), base_url="http://192.168.0.10:8081")


def read_zip(content: bytes, tmp_path):
    path = tmp_path / "got.zip"
    path.write_bytes(content)
    with zipfile.ZipFile(path) as z:
        return z.namelist(), json.loads(z.read("PaintApp/config.json"))


def test_zip_has_config(tmp_path, build):
    client = client_for(tmp_path, build)
    assert client.get("/api/paintapp").json()["available"] is True
    response = client.get("/download/PaintApp_7.zip")
    assert response.status_code == 200
    assert 'filename="PaintApp_07.zip"' in response.headers["content-disposition"]
    names, config = read_zip(response.content, tmp_path)
    assert config["serverUrl"] == "http://192.168.0.10:8081"
    assert config["studentId"] == "07"
    assert config["backupFolder"] == "B"  # 빌드 폴더 설정의 나머지 값은 유지
    assert "PaintApp/PaintApp.exe" in names and "PaintApp/PaintApp_Data/level0" in names
    assert not any("Backups" in n for n in names)
    assert names.count("PaintApp/config.json") == 1


def test_rebuild_after_change(tmp_path, build):
    client = client_for(tmp_path, build)
    client.get("/download/PaintApp_1.zip")
    (build / "PaintApp_Data" / "level0").write_bytes(b"new data!")
    names, _ = read_zip(client.get("/download/PaintApp_1.zip").content, tmp_path)
    with zipfile.ZipFile(tmp_path / "got.zip") as z:
        assert z.read("PaintApp/PaintApp_Data/level0") == b"new data!"


@pytest.mark.parametrize("pc", [0, 100, -1])
def test_bad_id(tmp_path, build, pc):
    assert client_for(tmp_path, build).get(f"/download/PaintApp_{pc}.zip").status_code == 400


def test_no_build(tmp_path):
    client = client_for(tmp_path, tmp_path / "missing")
    assert client.get("/api/paintapp").json()["available"] is False
    assert client.get("/download/PaintApp_1.zip").status_code == 404
    assert client.get("/download").status_code == 200


def test_server_bundle_download(tmp_path):
    client = TestClient(create_app(tmp_path / "data"))
    assert client.get("/download/PaintWorldServer.zip").status_code == 404
    share = tmp_path / "data" / "share"
    share.mkdir(parents=True)
    (share / "PaintWorldServer.zip").write_bytes(b"PK")
    response = client.get("/download/PaintWorldServer.zip")
    assert response.status_code == 200 and response.content == b"PK"

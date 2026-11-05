"""규약 API(/api/character, /api/characters, /api/scans) 테스트. Blender 대신 가짜 변환기를 씁니다."""

import json
import shutil
import struct
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import glb
from main import create_app
from scans import find_blender
from storage import ScanStore
from test_server import make_png

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "Contract" / "samples"
sys.path.insert(0, str(ROOT / "Contract" / "tools"))
from make_samples import make_cube_glb  # noqa: E402


def fake_converter(src: Path, dst: Path, thumb: Path) -> dict:
    """업로드 내용으로 결과를 정합니다: b"fail" → 변환 실패, b"huge" → 1m 정육면체(재검사에서 걸림), 그 외 → 10cm 정육면체."""
    content = src.read_bytes()
    if content == b"fail":
        return {"ok": False, "error": "가짜 실패"}
    dst.write_bytes(make_cube_glb(1.0 if content == b"huge" else 0.1))
    if content != b"no-thumb":
        thumb.write_bytes(make_png(256, 256))
    return {"ok": True, "removedIslands": 2, "floorRemoved": True, "holesFilled": 1}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, converter=fake_converter)


@pytest.fixture
def client(app):
    return TestClient(app)


def upload_character(client, character_id, data, png=None):
    return client.post(
        "/api/character",
        data={"id": str(character_id), "json": json.dumps(data, ensure_ascii=False)},
        files={"file": (f"{character_id}.png", png or make_png(), "image/png")},
    )


def upload_scan(client, app, name="초록 공룡", content=b"fake scan bytes"):
    res = client.post("/api/scans", data={"name": name}, files={"file": ("cup.usdz", content, "application/octet-stream")})
    assert res.status_code == 202, res.text
    app.state.jobs.wait_idle()
    return client.get(f"/api/scans/jobs/{res.json()['jobId']}").json()


def sample(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def character(character_id, name="민수", accessories=()):
    return {"id": character_id, "name": name, "texture": f"{character_id}.png", "accessories": list(accessories)}


def hat(bone="Head"):
    return {"itemId": "hat_01", "bone": bone, "pos": [0, 0.25, 0], "rot": [0, 0, 0], "scale": 1}


# ───────────────────────── 캐릭터 ─────────────────────────

def test_upload_and_download_character(client):
    png = make_png(rgb=(1, 2, 3))
    res = upload_character(client, 7, character(7, accessories=[hat()]), png)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["id"], body["version"], body["accessories"], body["warnings"]) == (7, 1, 1, [])
    assert body["png"] == "/characters/7.png?v=1"

    assert client.get("/characters/7.png").content == png
    saved = client.get("/characters/7.json").json()
    assert saved["name"] == "민수" and saved["accessories"][0]["itemId"] == "hat_01"
    assert saved["accessories"][0]["source"] == "builtin"


def test_id_is_normalized(client):
    assert upload_character(client, "07", character(7)).status_code == 200
    assert [c["id"] for c in client.get("/api/characters").json()["characters"]] == [7]


def test_reupload_bumps_version_and_list_is_sorted(client):
    for cid in (10, 2, 2):
        upload_character(client, cid, character(cid))
    listed = client.get("/api/characters").json()["characters"]
    assert [(c["id"], c["version"]) for c in listed] == [(2, 2), (10, 1)]


def test_server_stores_sanitized_json(client):
    """장식 7개(8.json)를 보내면 5개만 저장하고 경고를 돌려줍니다."""
    res = upload_character(client, 8, sample("8.json"))
    assert res.status_code == 200
    assert [w["code"] for w in res.json()["warnings"]] == ["TooManyAccessories", "TooManyAccessories"]
    assert len(client.get("/characters/8.json").json()["accessories"]) == 5


@pytest.mark.parametrize("data, png, reason", [
    (character(7, name="이름이너무너무길어요"), None, "name"),
    ({**character(7), "texture": "seven.png"}, None, "texture"),
    (character(8), None, "id 불일치"),
    (character(7), make_png(512, 512), "512px"),
])
def test_rejects_bad_header(client, data, png, reason):
    assert upload_character(client, 7, data, png).status_code == 400, reason


def test_accepts_2048_texture(client):
    assert upload_character(client, 7, character(7), make_png(2048, 2048)).status_code == 200


# ───────────────────────── 스캔 ─────────────────────────

def test_scan_upload_converts_and_registers(client, app):
    job = upload_scan(client, app, "  초록 공룡  ")
    assert job["status"] == "done", job
    assert job["itemId"].startswith("scan_") and job["file"] == f"scans/{job['itemId'][5:]}.glb"

    body = client.get("/api/scans").json()
    assert body["max"] == 40 and body["canManage"] is False
    scans = body["scans"]
    assert [s["itemId"] for s in scans] == [job["itemId"]]
    assert scans[0]["triangles"] == 12 and scans[0]["dimensions"] == [0.1, 0.1, 0.1]
    assert client.get(f"/{job['file']}").content[:4] == b"glTF"
    assert scans[0]["name"] == "초록 공룡" and "owner" not in scans[0]
    assert scans[0]["cleaning"] == {"removedIslands": 2, "floorRemoved": True, "holesFilled": 1}
    thumb = client.get(f"/{scans[0]['thumb']}")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/png"
    assert thumb.content == make_png(256, 256)


@pytest.mark.parametrize("content, reason", [(b"fail", "가짜 실패"), (b"huge", "최대 길이")])
def test_failed_scans_are_not_registered(client, app, content, reason):
    job = upload_scan(client, app, content=content)
    assert job["status"] == "failed" and reason in job["error"], job
    assert client.get("/api/scans").json()["scans"] == []


def test_rejects_unsupported_scan_format(client):
    res = client.post("/api/scans", data={"name": "사진"}, files={"file": ("photo.jpg", b"x", "image/jpeg")})
    assert res.status_code == 400


def test_scan_file_path_traversal_is_rejected(client):
    assert client.get("/scans/..%2Fcharacters.json").status_code == 404


def test_character_keeps_any_registered_scan_and_discards_unknown(client, app):
    mine = upload_scan(client, app, "컵")
    others = upload_scan(client, app, "공룡")
    accessories = [
        {"itemId": mine["itemId"], "source": "scan", "file": mine["file"], "bone": "Chest", "pos": [0, 0, 0.1], "rot": [0, 0, 0], "scale": 1},
        {"itemId": others["itemId"], "source": "scan", "file": others["file"], "bone": "Head", "pos": [0, 0, 0], "rot": [0, 0, 0], "scale": 1},
        {"itemId": "scan_00000000", "source": "scan", "file": "scans/00000000.glb", "bone": "Head", "pos": [0, 0, 0], "rot": [0, 0, 0], "scale": 1},
    ]
    res = upload_character(client, 7, character(7, accessories=accessories))
    assert res.status_code == 200
    assert [(w["code"], w["index"]) for w in res.json()["warnings"]] == [("UnknownScan", 2)]
    assert [a["itemId"] for a in client.get("/characters/7.json").json()["accessories"]] == [mine["itemId"], others["itemId"]]


@pytest.mark.parametrize("name", [None, "", "   ", "가" * 13])
def test_scan_requires_valid_display_name(client, name):
    data = {} if name is None else {"name": name}
    res = client.post("/api/scans", data=data, files={"file": ("cup.glb", b"scan")})
    assert res.status_code == 400


def test_scan_without_thumbnail_still_registers(client, app):
    assert upload_scan(client, app, content=b"no-thumb")["status"] == "done"
    assert client.get("/api/scans").json()["scans"][0]["thumb"] is None


def test_only_loopback_can_delete_scan_and_both_files_disappear(client, app):
    job = upload_scan(client, app)
    entry = client.get("/api/scans").json()["scans"][0]
    url = f"/api/scans/{job['itemId']}"
    assert client.delete(url).status_code == 403
    local = TestClient(app, client=("127.0.0.1", 12345))
    assert local.get("/api/scans").json()["canManage"] is True
    assert local.delete(url).status_code == 204
    assert local.get("/api/scans").json()["scans"] == []
    assert local.get(f"/{entry['file']}").status_code == 404
    assert local.get(f"/{entry['thumb']}").status_code == 404
    assert local.delete(url).status_code == 404
    assert ScanStore(app.state.scans.dir.parent).list() == []


def test_deleted_scan_is_removed_on_next_character_upload(client, app):
    job = upload_scan(client, app)
    accessory = {"itemId": job["itemId"], "source": "scan", "file": job["file"], "bone": "Head",
                 "pos": [0, 0, 0], "rot": [0, 0, 0], "scale": 1}
    assert upload_character(client, 7, character(7, accessories=[accessory])).json()["accessories"] == 1
    TestClient(app, client=("::1", 12345)).delete(f"/api/scans/{job['itemId']}")
    assert len(client.get("/characters/7.json").json()["accessories"]) == 1
    response = upload_character(client, 7, character(7, accessories=[accessory])).json()
    assert response["accessories"] == 0 and response["warnings"][0]["code"] == "UnknownScan"


def test_library_limit_counts_registered_scans(client, app):
    for _ in range(40):
        app.state.scans.add(make_cube_glb(), None, {"name": "컵"})
    res = client.post("/api/scans", data={"name": "넘침"}, files={"file": ("cup.glb", b"scan")})
    assert res.status_code == 409
    assert res.json()["detail"] == "스캔 장식이 가득 찼어요 (40개). 안 쓰는 스캔을 지워 주세요."


def test_library_limit_reserves_queued_and_converting_jobs(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def blocked_converter(src, dst, thumb):
        entered.set()
        assert release.wait(10)
        return {"ok": False, "error": "시험 실패"}

    app = create_app(tmp_path, converter=blocked_converter)
    client = TestClient(app)
    for _ in range(38):
        app.state.scans.add(make_cube_glb(), None, {"name": "컵"})
    try:
        for _ in range(2):
            assert client.post("/api/scans", data={"name": "대기"}, files={"file": ("cup.glb", b"scan")}).status_code == 202
        assert entered.wait(2)
        assert client.post("/api/scans", data={"name": "넘침"}, files={"file": ("cup.glb", b"scan")}).status_code == 409
    finally:
        release.set()
        app.state.jobs.wait_idle()
    assert upload_scan(client, app)["status"] == "failed"  # 실패한 작업은 자리를 비움


def test_legacy_owner_index_is_read_without_losing_scan(tmp_path):
    entry = {"itemId": "scan_ab12", "file": "scans/ab12.glb", "owner": 7, "createdAt": "2026-01-01"}
    (tmp_path / "scans.json").write_text(json.dumps({"scans": [entry]}), encoding="utf-8")
    store = ScanStore(tmp_path)
    scan = store.list()[0]
    assert scan["itemId"] == "scan_ab12" and scan["name"] == "스캔" and scan["thumb"] is None
    assert store.is_registered({"itemId": "scan_ab12", "file": "scans/ab12.glb"})
    assert not store.is_registered({"itemId": "scan_ab12", "file": "scans/other.glb"})


def test_glb_rejects_more_than_6000_triangles_and_1024_texture():
    data = make_cube_glb()
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_size])
    binary = data[28 + json_size:]
    indices = struct.pack("<H", 0) * (6001 * 3)
    document["accessors"][2]["count"] = 6001 * 3
    document["bufferViews"][2] = {"buffer": 0, "byteOffset": len(binary), "byteLength": len(indices)}
    binary += indices
    binary += b"\0" * (-len(binary) % 4)
    image = make_png(1024, 1024)
    document["images"] = [{"bufferView": len(document["bufferViews"]), "mimeType": "image/png"}]
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(image)})
    binary += image
    binary += b"\0" * (-len(binary) % 4)
    document["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    chunks = struct.pack("<I4s", len(encoded), b"JSON") + encoded + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    info = glb.inspect(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)
    assert info.triangles == 6001 and info.images == [(1024, 1024)]
    assert any("삼각형" in p for p in info.problems) and any("텍스처" in p for p in info.problems)


def test_glb_rejects_more_than_2mb():
    data = bytearray(make_cube_glb())
    json_size = struct.unpack_from("<I", data, 12)[0]
    data.extend(b"\0" * (2 * 1024 * 1024 + 4 - len(data)))
    struct.pack_into("<I", data, 8, len(data))
    struct.pack_into("<I", data, 20 + json_size, len(data) - 28 - json_size)
    assert any("파일" in p for p in glb.inspect(bytes(data)).problems)


# ───────────────────────── 실제 Blender (설치된 PC에서만) ─────────────────────────

@pytest.mark.skipif(not Path(find_blender()).exists() and shutil.which("blender") is None, reason="Blender 없음")
def test_real_blender_rejects_tiny_sample_after_cleaning(tmp_path):
    app = create_app(tmp_path)   # 기본 변환기 = 실제 Blender
    client = TestClient(app)
    res = client.post("/api/scans", data={"name": "작은 큐브"},
                      files={"file": ("cube.glb", (SAMPLES / "scans" / "ab12.glb").read_bytes(), "model/gltf-binary")})
    app.state.jobs.wait_idle(timeout=180)
    job = client.get(f"/api/scans/jobs/{res.json()['jobId']}").json()
    assert job["status"] == "failed" and "청소 후 남은 메쉬가 너무 작습니다" in job["error"], job

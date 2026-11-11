"""GPU·TRELLIS 의존성 없이 작업기의 HTTP 경계와 단일 생성 대기열을 검사합니다."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from worker.trellis_worker import create_app
from test_server import make_png


TOKEN = "test-only-worker-secret"
HEADERS = {"X-Worker-Token": TOKEN}


class Generator:
    def __init__(self):
        self.active = 0
        self.peak = 0

    def __call__(self, image, dst, cut, seed, faces):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            assert image.read_bytes() == make_png(8, 8)
            assert faces == 1500
            cut.write_bytes(make_png(8, 8))
            if seed == 2:
                raise RuntimeError("이 시드만 실패")
            dst.write_bytes(b"glTF" + bytes([seed]))
        finally:
            self.active -= 1


def wait_job(client, jid):
    for _ in range(500):
        state = client.get(f"/jobs/{jid}", headers=HEADERS).json()
        if state["status"] in {"done", "failed"}:
            return state
        time.sleep(.01)
    pytest.fail(str(state))


def submit(client, seeds="1,2,3", **kwargs):
    return client.post("/jobs", headers=HEADERS, data={"seeds": seeds, **kwargs},
                       files={"image": ("photo.png", make_png(8, 8), "image/png")})


def test_worker_auth_single_loader_partial_failure_files_and_expiration(tmp_path):
    generator = Generator()
    loads = []
    now = [0.0]

    def load():
        loads.append(True)
        return generator

    with TestClient(create_app(TOKEN, tmp_path, load, clock=lambda: now[0])) as client:
        for path in ("/health", "/docs", "/jobs/missing", "/jobs/missing/0.glb", "/jobs/missing/cut.png"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"X-Worker-Token": "wrong"}).status_code == 401
        ids = [submit(client).json()["jobId"] for _ in range(2)]
        for jid in ids:
            state = wait_job(client, jid)
            assert state["status"] == "done" and state["done"] == 3 and state["total"] == 3
            assert state["candidates"] == [True, False, True]
            assert client.get(f"/jobs/{jid}/0.glb", headers=HEADERS).content == b"glTF\x01"
            assert client.get(f"/jobs/{jid}/1.glb", headers=HEADERS).status_code == 404
            assert client.get(f"/jobs/{jid}/cut.png", headers=HEADERS).content == make_png(8, 8)
        assert generator.peak == 1 and loads == [True]
        assert client.get("/health", headers=HEADERS).json() == {"ok": True, "loaded": True, "queue": 0}
        now[0] = 3601
        assert client.get(f"/jobs/{ids[0]}", headers=HEADERS).status_code == 404
        assert not (tmp_path / ids[0]).exists()


@pytest.mark.parametrize("seeds", ["", "1,2,3,4,5", "-1", "2147483648", "x"])
def test_worker_rejects_invalid_seeds(tmp_path, seeds):
    with TestClient(create_app(TOKEN, tmp_path, Generator)) as client:
        assert submit(client, seeds).status_code == 400


def test_worker_failed_model_load_and_all_candidates_failed(tmp_path):
    def bad_load():
        raise RuntimeError("가중치 없음")

    with TestClient(create_app(TOKEN, tmp_path / "unloaded", bad_load)) as client:
        for _ in range(100):
            if not client.get("/health", headers=HEADERS).json()["ok"]:
                break
            time.sleep(.01)
        assert submit(client).status_code == 503
    with TestClient(create_app(TOKEN, tmp_path / "failed", Generator)) as client:
        jid = submit(client, "2").json()["jobId"]
        state = wait_job(client, jid)
        assert state["status"] == "failed" and state["candidates"] == [False] and state["done"] == 1


def test_worker_rejects_empty_token(tmp_path):
    with pytest.raises(ValueError):
        create_app("", tmp_path, Generator)

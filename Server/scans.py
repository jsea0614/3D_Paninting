"""스캔 변환 작업 대기열: 업로드 → Blender 변환(tools/convert_scan.py) → GLB 재검사(glb.py) → 통과한 것만 등록.

Blender는 무거워서 작업을 한 번에 하나씩 처리합니다. 변환에 실패하거나 재검사를 통과하지 못한 스캔은 등록하지 않으므로
Quest 앱까지 가지 않습니다.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import glb
from storage import ScanStore

log = logging.getLogger("paint-server")

SCAN_EXTENSIONS = {".glb", ".gltf", ".obj", ".fbx", ".usd", ".usdz", ".usdc", ".usda"}
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
CONVERT_SCRIPT = Path(__file__).parent / "tools" / "convert_scan.py"
MAX_LIBRARY = 40
MAX_PHOTO_BYTES = 20 * 1024 * 1024
PHOTO_EXPIRED_SECONDS = 30 * 60
GENERATION_TIMEOUT = 10 * 60
PENDING = {"queued", "generating", "converting", "choose"}

# (입력 경로, 출력 GLB 경로, 썸네일 PNG 경로) → 변환 보고서 dict
Converter = Callable[[Path, Path, Path], dict]


class LibraryFullError(ValueError):
    pass


class WorkerUnavailableError(ValueError):
    pass


class WorkerClient:
    """표준 라이브러리만 사용해 인증된 원격 작업기에 요청합니다."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def _request(self, path, data=None, content_type=None, *, timeout=30, binary=False):
        headers = {"X-Worker-Token": self.token}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=max(.01, timeout)) as response:
            limit = 32 * 1024 * 1024 if binary else 1024 * 1024
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError("작업기 응답이 너무 큽니다")
            return body if binary else json.loads(body)

    def health(self):
        return self._request("/health", timeout=5)

    def submit(self, source_name, data, seeds, *, timeout=30):
        boundary = "scan-" + secrets.token_hex(16)
        # 사용자 파일 이름은 multipart 헤더에 넣지 않습니다.
        suffix = ".png" if data.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="seeds"\r\n\r\n'
                + ",".join(map(str, seeds)) + f'\r\n--{boundary}\r\n'
                + 'Content-Disposition: form-data; name="faces"\r\n\r\n1500\r\n'
                + f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="photo{suffix}"\r\n'
                + 'Content-Type: application/octet-stream\r\n\r\n').encode()
        body += data + f"\r\n--{boundary}--\r\n".encode()
        return self._request("/jobs", body, f"multipart/form-data; boundary={boundary}", timeout=timeout)["jobId"]

    def status(self, job_id, *, timeout=30):
        return self._request("/jobs/" + urllib.parse.quote(job_id, safe=""), timeout=timeout)

    def download(self, job_id, index, *, timeout=30):
        return self._request(f"/jobs/{urllib.parse.quote(job_id, safe='')}/{index}.glb", timeout=timeout, binary=True)


def find_blender() -> str:
    """환경 변수 BLENDER → 기본 설치 위치 → PATH의 blender 순서로 찾습니다."""
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    if Path(DEFAULT_BLENDER).exists():
        return DEFAULT_BLENDER
    return shutil.which("blender") or "blender"


def blender_converter(blender: str, timeout: int = 180) -> Converter:
    def convert(src: Path, dst: Path, thumb: Path) -> dict:
        report_path = dst.with_suffix(".report.json")
        try:
            subprocess.run(
                [blender, "-b", "--factory-startup", "-P", str(CONVERT_SCRIPT), "--", str(src), str(dst), str(report_path), str(thumb)],
                capture_output=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"변환이 {timeout}초 안에 끝나지 않았습니다"}
        except OSError as e:
            return {"ok": False, "error": f"Blender를 실행하지 못했습니다 ({blender}): {e}"}
        if not report_path.exists():
            return {"ok": False, "error": "Blender가 보고서를 남기지 않았습니다"}
        return json.loads(report_path.read_text(encoding="utf-8"))
    return convert


@dataclass
class Job:
    jobId: str
    name: str
    sourceName: str
    status: str = "queued"          # queued → converting → done | failed
    itemId: str | None = None
    file: str | None = None
    error: str | None = None
    createdAt: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class PhotoJob(Job):
    kind: str = "photo"
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 3})
    candidates: list[dict] = field(default_factory=list)


class ScanJobs:
    def __init__(self, store: ScanStore, converter: Converter, work_dir: Path, *, worker=None, clock=time.monotonic):
        self.store = store
        self.converter = converter
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.worker, self.clock = worker, clock
        self._choices: dict[str, dict[int, tuple[Path, Path, dict]]] = {}
        self._expires: dict[str, float] = {}
        self.queue = queue.Queue()  # 기존 업로드, 사진 후보 묶음, wait_idle 표시
        threading.Thread(target=self._run, name="scan-worker", daemon=True).start()

    def submit(self, name: str, source_name: str, data: bytes) -> Job:
        with self.lock:
            self._check_capacity_locked()
            job = Job(uuid.uuid4().hex[:12], name, source_name)
            src = self.work_dir / f"{job.jobId}{Path(source_name).suffix.lower()}"
            src.write_bytes(data)
            self.jobs[job.jobId] = job
        self.queue.put((job, src))
        return job

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            self._expire_locked()
            job = self.jobs.get(job_id)
            return asdict(job) if job else None

    def _check_capacity_locked(self):
        self._expire_locked()
        pending = sum(j.status in PENDING for j in self.jobs.values())
        if len(self.store.list()) + pending >= MAX_LIBRARY:
            raise LibraryFullError("스캔 장식이 가득 찼어요 (40개). 안 쓰는 스캔을 지워 주세요.")

    def _clear_photo_locked(self, job_id):
        self._choices.pop(job_id, None)
        self._expires.pop(job_id, None)
        shutil.rmtree(self.work_dir / job_id, ignore_errors=True)

    def _expire_locked(self):
        for job_id, deadline in list(self._expires.items()):
            if self.clock() >= deadline:
                self.jobs[job_id].status = "discarded"
                self._clear_photo_locked(job_id)

    def worker_health(self):
        result = {"configured": self.worker is not None, "ok": False, "loaded": False, "queue": 0}
        if self.worker:
            try:
                health = self.worker.health()
                result.update({key: health[key] for key in ("ok", "loaded", "queue")})
            except Exception:
                pass  # 연결 상태만 중계하며 토큰·요청 객체는 노출하지 않습니다.
        return result

    def submit_photo(self, name, source_name, data):
        if self.worker is None:
            raise WorkerUnavailableError("3D 생성 PC가 설정되지 않았어요")
        with self.lock:
            self._check_capacity_locked()
            job = PhotoJob(uuid.uuid4().hex[:12], name, source_name)
            folder = self.work_dir / job.jobId
            folder.mkdir()
            self.jobs[job.jobId] = job
        threading.Thread(target=self._generate, args=(job, data), name="photo-generate", daemon=True).start()
        return job

    def _generate(self, job, data):
        self._update(job, status="generating")
        seeds = [secrets.randbelow(2**31) for _ in range(3)]
        deadline = self.clock() + GENERATION_TIMEOUT

        def remaining():
            value = deadline - self.clock()
            if value <= 0:
                raise TimeoutError("10분 시간 초과")
            return min(30, value)

        try:
            remote_id = self.worker.submit(job.sourceName, data, seeds, timeout=remaining())
            while True:
                state = self.worker.status(remote_id, timeout=remaining())
                self._update(job, progress={"done": state["done"], "total": state["total"]})
                if state["status"] == "done":
                    break
                if state["status"] == "failed":
                    if state.get("done") == state.get("total") == len(seeds) and "candidates" in state:
                        break  # 생성은 끝났지만 모든 시드가 실패: 아래 후보 0개 재촬영 안내로 처리
                    raise RuntimeError(state.get("error") or "생성 실패")
                time.sleep(min(5, remaining()))
            candidates, sources = [], []
            for index, seed in enumerate(seeds):
                candidate = {"index": index, "thumb": None, "triangles": 0, "ok": False, "error": None}
                candidates.append(candidate)
                if index >= len(state.get("candidates", [])) or not state["candidates"][index]:
                    candidate["error"] = "이 후보를 만들지 못했어요"
                    continue
                src = self.work_dir / job.jobId / f"{index}.source.glb"
                src.write_bytes(self.worker.download(remote_id, index, timeout=remaining()))
                sources.append((index, seed, src))
            self._update(job, status="converting", candidates=candidates)
            self.queue.put((job, sources))
        except Exception as e:
            with self.lock:
                job.status, job.error = "failed", f"3D 생성 PC에 연결할 수 없어요 ({e})"
                self._clear_photo_locked(job.jobId)

    def _convert_photo(self, job, sources):
        choices = {}
        for index, seed, src in sources:
            candidate = job.candidates[index].copy()
            dst = src.parent / f"{index}.glb"
            thumb = src.parent / f"{index}.png"
            try:
                report = self.converter(src, dst, thumb)
                if not report.get("ok") or not dst.exists():
                    raise ValueError(report.get("error") or "변환 실패")
                info = glb.inspect(dst.read_bytes())
                if not info.ok:
                    raise ValueError("; ".join(info.problems))
                if not thumb.exists():
                    raise ValueError("후보 미리보기를 만들지 못했어요")
                metadata = self._metadata(job, info, report)
                metadata["seed"] = seed
                choices[index] = (dst, thumb, metadata)
                candidate.update(ok=True, triangles=info.triangles,
                                 thumb=f"/api/scans/jobs/{job.jobId}/candidates/{index}.png")
            except Exception as e:
                candidate["error"] = str(e)
            with self.lock:
                job.candidates[index] = candidate
        with self.lock:
            if choices:
                self._choices[job.jobId] = choices
                self._expires[job.jobId] = self.clock() + PHOTO_EXPIRED_SECONDS
                job.status = "choose"
            else:
                job.status, job.error = "failed", "3D로 만들지 못했어요. 정면에서 조금 위로 다시 찍어 주세요"
                self._clear_photo_locked(job.jobId)

    def _photo_locked(self, job_id):
        self._expire_locked()
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not isinstance(job, PhotoJob) or job.status != "choose":
            raise ValueError("후보를 고를 수 있는 상태가 아니에요")
        return job

    def choose(self, job_id, index):
        with self.lock:
            job = self._photo_locked(job_id)
            choice = self._choices[job_id].get(index) if type(index) is int else None
            if choice is None:
                raise ValueError("고를 수 없는 후보예요")
            if len(self.store.list()) >= MAX_LIBRARY:
                raise LibraryFullError("스캔 장식이 가득 찼어요 (40개). 안 쓰는 스캔을 지워 주세요.")
            dst, thumb, metadata = choice
            entry = self.store.add(dst.read_bytes(), thumb.read_bytes(), metadata)
            job.status, job.itemId, job.file = "done", entry["itemId"], entry["file"]
            self._clear_photo_locked(job_id)
            return entry

    def discard(self, job_id):
        with self.lock:
            job = self._photo_locked(job_id)
            job.status = "discarded"
            self._clear_photo_locked(job_id)
            return asdict(job)

    def candidate_thumbnail(self, job_id, index):
        with self.lock:
            self._expire_locked()
            choice = self._choices.get(job_id, {}).get(index)
            return choice[1].read_bytes() if choice else None

    def wait_idle(self, timeout: float = 30) -> None:
        """테스트용: 대기열의 작업이 모두 끝날 때까지 기다립니다."""
        done = threading.Event()
        self.queue.put((None, done))  # type: ignore[arg-type]
        done.wait(timeout)

    def _run(self) -> None:
        while True:
            try:
                job, src = self.queue.get(timeout=5)
            except queue.Empty:
                with self.lock:
                    self._expire_locked()
                continue
            if job is None:          # wait_idle 표시
                src.set()
                continue
            if isinstance(job, PhotoJob):
                try:
                    self._convert_photo(job, src)
                except Exception as e:
                    log.exception("사진 후보 변환 중 오류")
                    with self.lock:
                        job.status, job.error = "failed", f"후보 변환 실패: {e}"
                        self._clear_photo_locked(job.jobId)
                continue
            self._update(job, status="converting")
            with tempfile.TemporaryDirectory(dir=self.work_dir) as tmp:
                dst = Path(tmp) / "out.glb"
                try:
                    self._convert(job, src, dst)
                except Exception as e:  # noqa: BLE001 - 작업 하나의 실패가 대기열을 멈추지 않게
                    log.exception("스캔 변환 중 오류")
                    self._update(job, status="failed", error=f"서버 오류: {e}")
            src.unlink(missing_ok=True)

    def _convert(self, job: Job, src: Path, dst: Path) -> None:
        thumb = dst.with_suffix(".png")
        report = self.converter(src, dst, thumb)
        if not report.get("ok") or not dst.exists():
            self._update(job, status="failed", error=report.get("error") or "변환 실패")
            log.info("스캔 실패: %s, %s: %s", job.name, job.sourceName, job.error)
            return
        data = dst.read_bytes()
        try:
            info = glb.inspect(data)
        except ValueError as e:
            self._update(job, status="failed", error=f"GLB 검사 실패: {e}")
            return
        if not info.ok:
            self._update(job, status="failed", error="경량화 기준 미달: " + "; ".join(info.problems))
            return
        # 접수와 같은 잠금으로 등록·완료를 묶어 상한 계산에서 두 번 세지 않게 합니다.
        with self.lock:
            entry = self.store.add(data, thumb.read_bytes() if thumb.exists() else None, self._metadata(job, info, report))
            job.status, job.itemId, job.file = "done", entry["itemId"], entry["file"]
        log.info("스캔 등록: %s, %s → %s (삼각형 %d, %d KB)", job.name, job.sourceName, entry["itemId"], info.triangles, info.bytes // 1024)

    @staticmethod
    def _metadata(job, info, report):
        return {
            "name": job.name,
            "sourceName": job.sourceName,
            "triangles": info.triangles,
            "bytes": info.bytes,
            "textureSize": list(info.images[0]) if info.images else None,
            "dimensions": [round(s, 4) for s in info.size],
            "cleaning": {key: report.get(key, default) for key, default in
                         (("removedIslands", 0), ("floorRemoved", False), ("holesFilled", 0))},
        }

    def _update(self, job: Job, **changes) -> None:
        with self.lock:
            for key, value in changes.items():
                setattr(job, key, value)

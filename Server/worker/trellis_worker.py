"""TRELLIS.2 전용 venv에서 실행하는 인증된 GPU 단일 작업기.

서버 패키지에는 torch/Pillow/TRELLIS 의존성을 추가하지 않습니다.
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

MAX_IMAGE_BYTES = 20 * 1024 * 1024
RESULT_TTL = 3600
log = logging.getLogger("trellis-worker")


class TrellisGenerator:
    def __init__(self, trellis: Path):
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        sys.path.insert(0, str(trellis.expanduser()))
        import torch
        from trellis2.pipelines import rembg, Trellis2ImageTo3DPipeline

        original = rembg.BiRefNet

        class MitBiRefNet(original):
            def __init__(self, model_name=None):
                super().__init__("ZhengPeng7/BiRefNet")
                self.model.float()

        rembg.BiRefNet = MitBiRefNet
        self.torch = torch
        self.pipe = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
        self.pipe.cuda()

    def __call__(self, image_path, dst, cut, seed, faces):
        from PIL import Image, ImageOps
        import o_voxel

        try:
            with Image.open(image_path) as source:
                img = ImageOps.exif_transpose(source).convert("RGB")
            if not cut.exists():
                self.pipe.preprocess_image(img.copy()).save(cut)
            mesh = self.pipe.run(img, seed=seed, pipeline_type="1024_cascade")[0]
            mesh.simplify(16777216)
            result = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices.clone(), faces=mesh.faces.clone(), attr_volume=mesh.attrs,
                coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]], decimation_target=faces,
                texture_size=1024, remesh=False, remesh_band=1, remesh_project=0.9,
            )
            result.export(str(dst))  # PNG 텍스처: webp 확장을 사용하지 않습니다.
        except self.torch.cuda.OutOfMemoryError:
            self.torch.cuda.empty_cache()
            raise


class Worker:
    def __init__(self, folder, loader, clock):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.loader, self.clock = loader, clock
        self.loaded, self.load_error = False, None
        self.jobs, self.finished = {}, {}
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="trellis-gpu", daemon=True)

    def cleanup(self):
        with self.lock:
            for jid, finished in list(self.finished.items()):
                if self.clock() - finished >= RESULT_TTL:
                    self.jobs.pop(jid, None)
                    self.finished.pop(jid, None)
                    shutil.rmtree(self.folder / jid, ignore_errors=True)
            # 재시작으로 사라진 작업도 디스크에서 정리합니다. 작업기 소유 UUID 폴더만 대상입니다.
            for path in self.folder.iterdir():
                if (path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name)
                        and path.name not in self.jobs and time.time() - path.stat().st_mtime >= RESULT_TTL):
                    shutil.rmtree(path, ignore_errors=True)

    def submit(self, data, seeds, faces):
        with self.lock:
            if self.load_error:
                raise HTTPException(503, "모델을 불러오지 못했어요. 작업기 로그를 확인해 주세요.")
            # 사진이 쌓여 디스크를 채우지 않도록 공유 라이브러리와 같은 최대 작업 수를 허용합니다.
            if sum(j["status"] in {"queued", "running"} for j in self.jobs.values()) >= 40:
                raise HTTPException(429, "생성 대기열이 가득 찼어요")
            jid = uuid.uuid4().hex
            folder = self.folder / jid
            folder.mkdir()
            (folder / "image").write_bytes(data)
            self.jobs[jid] = {"status": "queued", "done": 0, "total": len(seeds), "error": None,
                              "candidates": [False] * len(seeds)}
            self.queue.put((jid, seeds, faces))
            return jid

    def get(self, jid):
        with self.lock:
            state = self.jobs.get(jid)
            return {**state, "candidates": state["candidates"][:]} if state else None

    def read(self, jid, filename):
        with self.lock:
            if jid not in self.jobs:
                return None
            try:
                return (self.folder / jid / filename).read_bytes()
            except FileNotFoundError:
                return None

    def _run(self):
        try:
            generate = self.loader()
            with self.lock:
                self.loaded = True
        except Exception as e:
            log.exception("TRELLIS 모델 로드 실패")
            with self.lock:
                self.load_error = str(e)
                for jid, job in self.jobs.items():
                    job.update(status="failed", error="모델을 불러오지 못했어요")
                    self.finished[jid] = self.clock()
            return
        while not self.stop.is_set():
            self.cleanup()
            try:
                jid, seeds, faces = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            with self.lock:
                job = self.jobs[jid]
                job["status"] = "running"
            folder = self.folder / jid
            for index, seed in enumerate(seeds):
                dst = folder / f"{index}.glb"
                error = None
                try:
                    generate(folder / "image", dst, folder / "cut.png", seed, faces)
                    if not dst.is_file():
                        raise RuntimeError("생성 결과가 없습니다")
                except Exception as e:
                    log.exception("작업 %s 후보 %d 생성 실패", jid, index)
                    dst.unlink(missing_ok=True)
                    error = str(e)
                with self.lock:
                    job["candidates"][index] = error is None
                    job["done"] = index + 1
                    if error:
                        job["error"] = error
            with self.lock:
                job["status"] = "done" if any(job["candidates"]) else "failed"
                self.finished[jid] = self.clock()
            (folder / "image").unlink(missing_ok=True)


def create_app(token: str, output_dir: Path, loader=None, *, clock=time.monotonic, trellis=Path("~/TRELLIS.2")):
    if not token.strip():
        raise ValueError("비어 있지 않은 --token이 필요합니다")
    worker = Worker(output_dir, loader or (lambda: TrellisGenerator(trellis)), clock)

    @asynccontextmanager
    async def lifespan(app):
        worker.thread.start()
        try:
            yield
        finally:
            worker.stop.set()
            worker.thread.join(timeout=2)

    app = FastAPI(title="TRELLIS GPU Worker", lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        supplied = request.headers.get("X-Worker-Token", "")
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            return JSONResponse({"detail": "인증에 실패했습니다"}, status_code=401)
        await run_in_threadpool(worker.cleanup)
        return await call_next(request)

    @app.get("/health")
    def health():
        with worker.lock:
            return {"ok": worker.load_error is None, "loaded": worker.loaded, "queue": worker.queue.qsize()}

    @app.post("/jobs", status_code=202)
    async def submit_job(image: Annotated[UploadFile, File()], seeds: Annotated[str, Form()] = "",
                         faces: Annotated[int, Form()] = 1500):
        try:
            values = [int(s.strip()) for s in seeds.split(",")]
        except ValueError as e:
            raise HTTPException(400, "시드는 쉼표로 구분한 정수여야 합니다") from e
        if not 1 <= len(values) <= 4 or any(s < 0 or s >= 2**31 for s in values):
            raise HTTPException(400, "시드는 0~2147483647 정수 1~4개여야 합니다")
        if not 1 <= faces <= 2000:
            raise HTTPException(400, "삼각형 목표는 1~2000이어야 합니다")
        data = await image.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "사진이 20MB를 넘습니다")
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            raise HTTPException(400, "JPG 또는 PNG 사진이 아닙니다")
        # 디스크 쓰기는 이벤트 루프 밖에서 수행합니다.
        return {"jobId": await run_in_threadpool(worker.submit, data, values, faces)}

    @app.get("/jobs/{jid}")
    def job_status(jid: str):
        state = worker.get(jid)
        if state is None:
            raise HTTPException(404, "작업이 없습니다")
        return state

    @app.get("/jobs/{jid}/cut.png")
    def cut_image(jid: str):
        return result(jid, "cut.png", "image/png")

    @app.get("/jobs/{jid}/{index}.glb")
    def candidate(jid: str, index: int):
        state = worker.get(jid)
        if state is None or not 0 <= index < len(state["candidates"]) or not state["candidates"][index]:
            raise HTTPException(404, "후보가 없습니다")
        return result(jid, f"{index}.glb", "model/gltf-binary")

    def result(jid, filename, media_type):
        data = worker.read(jid, filename)
        if data is None:
            raise HTTPException(404, "파일이 없습니다")
        return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})

    return app


def main():
    parser = argparse.ArgumentParser(description="TRELLIS.2 GPU 작업기 (TRELLIS venv에서 실행)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--token", required=True)
    parser.add_argument("--trellis", type=Path, default=Path("~/TRELLIS.2"))
    args = parser.parse_args()
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = create_app(args.token, Path("~/scanlab/jobs").expanduser(), trellis=args.trellis)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

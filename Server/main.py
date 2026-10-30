"""원주시 직업체험 VR - 로컬 업로드 서버.

학생 PC의 색칠 앱이 캐릭터(PNG + JSON)를, iPad가 스캔 파일을 올리고, Quest 앱이 같은 Wi-Fi에서 받아 갑니다.
인터넷 연결 없이 운영 PC 한 대에서 실행합니다. 데이터 규약: Contract/CONTRACT.md

    uv run main.py --port 8081 [--blender <blender.exe 경로>]

API (규약)
    POST /api/character              multipart: id, json(문자열), file(PNG 1024 또는 2048)
    GET  /api/characters             캐릭터 목록 (id, name, version, png, json URL)
    GET  /characters/{id}.png|.json  최신 파일
    POST /api/scans                  multipart: id(소유 학생), file(.usdz .glb .obj .fbx 등) → 변환 작업
    GET  /api/scans/jobs/{jobId}     변환 작업 상태 (queued, converting, done, failed)
    GET  /api/scans?owner={id}       등록된 스캔 목록
    GET  /scans/{file}.glb           스캔 GLB
    GET  /scan                       iPad용 스캔 업로드 페이지
    GET  /api/health                 연결 확인
    POST /api/logs                   기기 로그 수집 (JSON, 최대 256KB/200개)
    GET  /api/logs                   기기 로그 파일 목록
    GET  /download                   학생 PC용 색칠 앱 받기 페이지
    GET  /api/paintapp               색칠 앱 빌드 정보 (available, sizeMB, builtAt)
    GET  /download/PaintApp_{n}.zip  PC 번호 n용 색칠 앱 (config.json에 serverUrl·studentId가 들어 있음)

이전 API (색칠 앱을 /api/character로 옮기면 제거)
    POST /api/texture, GET /textures/{studentId}.png, GET /api/players
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

import contract
from logs import DeviceLogs, MAX_BODY
from paintapp_dist import PaintAppDist
from scans import SCAN_EXTENSIONS, Converter, ScanJobs, blender_converter, find_blender
from storage import CharacterStore, ScanStore

TEXTURE_SIZE = 1024
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_CHARACTER_PNG_BYTES = 16 * 1024 * 1024
MAX_SCAN_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_ITEM_IDS = Path(__file__).resolve().parents[1] / "Contract" / "item_ids.json"
STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PAINTAPP_DIR = Path(__file__).resolve().parents[1] / "Builds" / "PaintApp"
MAX_PC_ID = 99
HEADER_ISSUES = {contract.BAD_ID, contract.BAD_NAME, contract.BAD_TEXTURE}
ID_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

log = logging.getLogger("paint-server")


def read_png_size(data: bytes) -> tuple[int, int] | None:
    """PNG 헤더(IHDR)에서 가로·세로를 읽습니다. PNG가 아니면 None."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


class PlayerStore:
    """학생별 최신 텍스처와 매니페스트(players.json)를 관리합니다.

    텍스처는 업로드마다 새 파일(textures/{studentId}/{version}.png)로 저장합니다.
    Windows에서는 다운로드 중인 파일을 덮어쓸 수 없기 때문입니다.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.textures_dir = data_dir / "textures"
        self.manifest_path = data_dir / "players.json"
        self.lock = threading.Lock()
        self.textures_dir.mkdir(parents=True, exist_ok=True)
        self.players: dict[str, dict] = self._load()
        self._cleanup_old_versions()

    def _load(self) -> dict[str, dict]:
        if not self.manifest_path.exists():
            return {}
        with self.manifest_path.open(encoding="utf-8") as f:
            return {p["studentId"]: p for p in json.load(f)["players"]}

    def _save(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"players": self.list()}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.manifest_path)

    def _cleanup_old_versions(self, student_id: str | None = None) -> None:
        ids = [student_id] if student_id else list(self.players)
        for sid in ids:
            current = f"{self.players[sid]['version']}.png"
            for f in (self.textures_dir / sid).glob("*.png"):
                if f.name != current:
                    try:
                        f.unlink()
                    except OSError:
                        pass  # 다운로드 중이면 다음 정리 때 지움

    def texture_path(self, student_id: str) -> Path | None:
        player = self.players.get(student_id)
        if player is None:
            return None
        return self.textures_dir / student_id / f"{player['version']}.png"

    def read_texture(self, student_id: str, attempts: int = 3) -> bytes | None:
        """최신 텍스처를 읽습니다. 경로를 받은 직후 새 업로드가 이전 파일을 지웠으면 새 경로로 다시 읽습니다."""
        for _ in range(attempts):
            path = self.texture_path(student_id)
            if path is None:
                return None
            try:
                return path.read_bytes()
            except FileNotFoundError:
                continue
        return None

    def list(self) -> list[dict]:
        return sorted(self.players.values(), key=lambda p: (len(p["studentId"]), p["studentId"]))

    def save_texture(self, student_id: str, character_id: str, png: bytes) -> dict:
        with self.lock:
            previous = self.players.get(student_id)
            version = previous["version"] + 1 if previous else 1
            folder = self.textures_dir / student_id
            folder.mkdir(exist_ok=True)
            (folder / f"{version}.png").write_bytes(png)

            player = {
                "studentId": student_id,
                "characterId": character_id,
                "version": version,
                "textureUrl": f"/textures/{student_id}.png?v={version}",
                "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self.players[student_id] = player
            self._save()
            self._cleanup_old_versions(student_id)
            return player


def create_app(data_dir: Path, converter: Converter | None = None, item_ids_path: Path = DEFAULT_ITEM_IDS,
               paintapp_dir: Path = DEFAULT_PAINTAPP_DIR) -> FastAPI:
    app = FastAPI(title="Paint Server")
    store = PlayerStore(data_dir)
    characters = CharacterStore(data_dir)
    scan_store = ScanStore(data_dir)
    jobs = ScanJobs(scan_store, converter or blender_converter(find_blender()), data_dir / "scan_jobs")
    item_ids = json.loads(item_ids_path.read_text(encoding="utf-8"))
    known_items = {i["itemId"] for i in item_ids["items"]}
    known_bones = set(item_ids["bones"])
    app.state.store = store
    app.state.characters = characters
    app.state.scans = scan_store
    app.state.jobs = jobs
    device_logs = DeviceLogs(data_dir / "logs")
    paintapp = PaintAppDist(paintapp_dir, data_dir / "dist")

    @app.post("/api/logs")
    async def append_logs(request: Request):
        body = await request.body()
        if len(body) > MAX_BODY:
            raise HTTPException(413, "로그 본문이 256KB를 넘습니다.")
        try:
            batch = json.loads(body)
        except (ValueError, UnicodeDecodeError) as e:
            raise HTTPException(400, "잘못된 로그 JSON입니다.") from e
        await run_in_threadpool(device_logs.append, batch)
        return {"ok": True, "written": len(batch["entries"])}

    @app.get("/api/logs")
    def list_logs():
        return {"devices": device_logs.list()}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "players": len(store.players), "characters": len(characters.index)}

    # ───────────────────────── 캐릭터 (규약) ─────────────────────────

    @app.post("/api/character")
    async def upload_character(
        id: Annotated[str, Form()],
        json_text: Annotated[str, Form(alias="json")],
        file: Annotated[UploadFile, File()],
    ):
        character_id = contract.normalize_id(id)
        if character_id is None:
            raise HTTPException(400, f"id는 1 이상의 정수여야 합니다: {id!r}")
        png = await file.read(MAX_CHARACTER_PNG_BYTES + 1)
        if len(png) > MAX_CHARACTER_PNG_BYTES:
            raise HTTPException(413, "PNG가 너무 큽니다.")
        size = read_png_size(png)
        if size is None:
            raise HTTPException(400, "PNG 파일이 아닙니다.")
        if size[0] != size[1] or size[0] not in contract.TEXTURE_SIZES:
            raise HTTPException(400, f"텍스처는 1024x1024 또는 2048x2048이어야 합니다. (받은 크기 {size[0]}x{size[1]})")
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON을 읽지 못했습니다: {e}") from e
        if not isinstance(data, dict) or data.get("id") != character_id:
            raise HTTPException(400, f"JSON의 id가 업로드 id({character_id})와 다릅니다.")

        result = contract.sanitize(
            data, known_items.__contains__, known_bones.__contains__,
            lambda entry: scan_store.is_registered_for(character_id, entry),
        )
        header = [i for i in result.issues if i.code in HEADER_ISSUES]
        if header:
            raise HTTPException(400, {"message": "캐릭터 정보가 규약에 맞지 않습니다.", "issues": [vars(i) for i in header]})

        saved = await run_in_threadpool(characters.save, character_id, png, result.data)
        warnings = [vars(i) for i in result.issues]
        log.info("캐릭터: %d '%s' v%d, 장식 %d개, 경고 %d개", character_id, result.data["name"], saved["version"],
                 len(result.data["accessories"]), len(warnings))
        return {**saved, "accessories": len(result.data["accessories"]), "warnings": warnings}

    @app.get("/api/characters")
    def list_characters():
        return {"characters": characters.list()}

    @app.get("/characters/{character_id}.png")
    def character_png(character_id: int):
        data = characters.read(character_id, ".png")
        if data is None:
            raise HTTPException(404, "캐릭터가 없습니다.")
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/characters/{character_id}.json")
    def character_json(character_id: int):
        data = characters.read(character_id, ".json")
        if data is None:
            raise HTTPException(404, "캐릭터가 없습니다.")
        return Response(data, media_type="application/json; charset=utf-8", headers={"Cache-Control": "no-cache"})

    # ───────────────────────── 스캔 ─────────────────────────

    @app.post("/api/scans", status_code=202)
    async def upload_scan(id: Annotated[str, Form()], file: Annotated[UploadFile, File()]):
        owner = contract.normalize_id(id)
        if owner is None:
            raise HTTPException(400, f"id는 1 이상의 정수여야 합니다: {id!r}")
        name = Path(file.filename or "scan").name
        if Path(name).suffix.lower() not in SCAN_EXTENSIONS:
            raise HTTPException(400, f"지원하지 않는 형식입니다. ({', '.join(sorted(SCAN_EXTENSIONS))})")
        data = await file.read(MAX_SCAN_UPLOAD_BYTES + 1)
        if len(data) > MAX_SCAN_UPLOAD_BYTES:
            raise HTTPException(413, "스캔 파일이 200MB를 넘습니다.")
        job = await run_in_threadpool(jobs.submit, owner, name, data)
        log.info("스캔 접수: 학생 %d, %s (%d KB), 작업 %s", owner, name, len(data) // 1024, job.jobId)
        return jobs.get(job.jobId)

    @app.get("/api/scans/jobs/{job_id}")
    def scan_job(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "작업이 없습니다.")
        return job

    @app.get("/api/scans")
    def list_scans(owner: int | None = None):
        return {"scans": scan_store.list(owner)}

    @app.get("/scans/{file_name}")
    def scan_file(file_name: str):
        data = scan_store.read(file_name)
        if data is None:
            raise HTTPException(404, "스캔이 없습니다.")
        # 스캔 파일은 바뀌지 않으므로(ID마다 새 파일) 캐시해도 됩니다.
        return Response(data, media_type="model/gltf-binary", headers={"Cache-Control": "max-age=86400"})

    @app.get("/scan")
    def scan_page():
        return FileResponse(STATIC_DIR / "scan.html", media_type="text/html; charset=utf-8")

    # ───────────────────────── 색칠 앱 배포 ─────────────────────────

    @app.get("/download")
    def download_page():
        return FileResponse(STATIC_DIR / "download.html", media_type="text/html; charset=utf-8")

    @app.get("/api/paintapp")
    def paintapp_info():
        return paintapp.info()

    @app.get("/download/PaintApp_{pc_id}.zip")
    async def paintapp_zip(pc_id: int, request: Request):
        if not 1 <= pc_id <= MAX_PC_ID:
            raise HTTPException(400, f"PC 번호는 1~{MAX_PC_ID}입니다.")
        # 학생 PC가 이 주소로 접속했으니 앱도 같은 주소로 서버를 찾는다.
        server_url = str(request.base_url).rstrip("/")
        path = await run_in_threadpool(paintapp.zip_for, pc_id, server_url)
        if path is None:
            raise HTTPException(404, "색칠 앱 빌드가 없습니다. 3D_Paninting에서 Tools/Build/Paint App (Windows)를 먼저 실행하세요.")
        log.info("색칠 앱 받기: PC %d, %s", pc_id, server_url)
        return FileResponse(path, media_type="application/zip", filename=f"PaintApp_{pc_id:02d}.zip")

    # ───────────────────────── 이전 API ─────────────────────────

    @app.get("/api/players")
    def players():
        return {"players": store.list()}

    @app.post("/api/texture")
    async def upload_texture(
        studentId: Annotated[str, Form(pattern=ID_PATTERN)],
        characterId: Annotated[str, Form(pattern=ID_PATTERN)],
        file: Annotated[UploadFile, File()],
    ):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"파일이 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB를 넘습니다.")
        size = read_png_size(data)
        if size is None:
            raise HTTPException(400, "PNG 파일이 아닙니다.")
        if size != (TEXTURE_SIZE, TEXTURE_SIZE):
            raise HTTPException(400, f"텍스처는 {TEXTURE_SIZE}x{TEXTURE_SIZE}이어야 합니다. (받은 크기 {size[0]}x{size[1]})")

        player = await run_in_threadpool(store.save_texture, studentId, characterId, data)
        log.info("업로드: 학생 %s, 캐릭터 %s, v%d, %d KB", studentId, characterId, player["version"], len(data) // 1024)
        return player

    @app.get("/textures/{studentId}.png")
    def texture(studentId: Annotated[str, PathParam(pattern=ID_PATTERN)]):
        # 파일을 열어 둔 채 응답하지 않고 바로 메모리로 읽습니다. 응답 도중 새 업로드가 이전 파일을 지워도 안전합니다.
        data = store.read_texture(studentId)
        if data is None:
            raise HTTPException(404, "텍스처가 없습니다.")
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="원주시 직업체험 VR 로컬 업로드 서버")
    parser.add_argument("--host", default="0.0.0.0", help="바인드 주소 (기본: 모든 네트워크)")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--blender", default=None, help="blender.exe 경로 (기본: 환경 변수 BLENDER → 기본 설치 위치 → PATH)")
    parser.add_argument("--paintapp-dir", type=Path, default=DEFAULT_PAINTAPP_DIR, help="/download로 나눠 줄 색칠 앱 빌드 폴더")
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    blender = args.blender or find_blender()
    log.info("Blender: %s", blender)
    uvicorn.run(create_app(args.data_dir, blender_converter(blender), paintapp_dir=args.paintapp_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

"""학생 PC용 색칠 앱 배포 zip.

빌드 폴더(Builds/PaintApp)를 한 번 압축해 두고(base), PC 번호마다 그 zip을 복사한 뒤 config.json만 덧붙입니다.
다시 압축하지 않으므로 번호별 zip은 금방 만들어집니다. 빌드가 바뀌면(파일 크기·수정 시각) base부터 다시 만듭니다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

EXE_NAME = "PaintApp.exe"
SKIP_TOP = {"config.json", "Backups"}  # PC마다 다른 설정, 그 PC의 그림 백업
DEFAULT_CONFIG = {"characterId": "base", "healthCheckSeconds": 10, "backupFolder": "Backups"}


class PaintAppDist:
    def __init__(self, build_dir: Path, cache_dir: Path):
        self.build_dir = build_dir
        self.cache_dir = cache_dir
        self.lock = threading.Lock()

    def _files(self) -> list[Path]:
        return sorted(p for p in self.build_dir.rglob("*")
                      if p.is_file() and p.relative_to(self.build_dir).parts[0] not in SKIP_TOP)

    def _signature(self, files: list[Path]) -> str:
        h = hashlib.sha1()
        for p in files:
            st = p.stat()
            h.update(f"{p.relative_to(self.build_dir).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        return h.hexdigest()[:12]

    def info(self) -> dict:
        exe = self.build_dir / EXE_NAME
        if not exe.is_file():
            return {"available": False, "buildDir": str(self.build_dir)}
        files = self._files()
        return {
            "available": True,
            "sizeMB": round(sum(p.stat().st_size for p in files) / (1024 * 1024)),
            "builtAt": datetime.fromtimestamp(exe.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }

    def _base_config(self) -> dict:
        path = self.build_dir / "config.json"
        try:
            return {**DEFAULT_CONFIG, **json.loads(path.read_text(encoding="utf-8-sig"))}
        except (OSError, ValueError):
            return dict(DEFAULT_CONFIG)

    def zip_for(self, pc_id: int, server_url: str) -> Path | None:
        """PC 번호용 zip 경로. 빌드가 없으면 None."""
        if not (self.build_dir / EXE_NAME).is_file():
            return None
        with self.lock:
            files = self._files()
            sig = self._signature(files)
            folder = self.cache_dir / sig
            base = folder / "base.zip"
            if not base.exists():
                if self.cache_dir.exists():
                    shutil.rmtree(self.cache_dir, ignore_errors=True)  # 이전 빌드의 zip (받는 중인 파일은 남을 수 있음)
                folder.mkdir(parents=True, exist_ok=True)
                tmp = base.with_suffix(".tmp")
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    for p in files:
                        z.write(p, f"PaintApp/{p.relative_to(self.build_dir).as_posix()}")
                tmp.replace(base)

            url_key = hashlib.sha1(server_url.encode()).hexdigest()[:8]
            target = folder / f"PaintApp_{pc_id:02d}_{url_key}.zip"
            if not target.exists():
                config = {**self._base_config(), "serverUrl": server_url, "studentId": f"{pc_id:02d}"}
                tmp = target.with_suffix(".tmp")
                shutil.copyfile(base, tmp)
                with zipfile.ZipFile(tmp, "a") as z:
                    z.writestr("PaintApp/config.json", json.dumps(config, ensure_ascii=False, indent=4))
                tmp.replace(target)
            return target

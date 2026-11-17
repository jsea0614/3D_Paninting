"""Quest 손 카메라 사진 저장 (POST /api/photos). 운영자가 data/photos 폴더에서 꺼내 쓴다."""
import re
import threading
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from logs import DEVICE_RE

MAX_PHOTO = 4 * 1024 * 1024
JPEG_SIGNATURE = b"\xff\xd8\xff"


class PhotoStore:
    def __init__(self, photos_dir: Path, clock=datetime.now):
        self.photos_dir = photos_dir
        self.clock = clock
        self.lock = threading.Lock()

    def save(self, data: bytes, device: str, character_id: int) -> str:
        if re.fullmatch(DEVICE_RE, device) is None:
            raise HTTPException(400, "잘못된 기기 이름입니다.")
        if len(data) > MAX_PHOTO:
            raise HTTPException(413, "사진이 4MB를 넘습니다.")
        if not data.startswith(JPEG_SIGNATURE):
            raise HTTPException(400, "JPEG 사진이 아닙니다.")
        who = f"{character_id:02d}번" if 1 <= character_id <= 99 else "번호없음"
        stem = f"{who}_{self.clock().strftime('%Y%m%d-%H%M%S')}_{device}"
        with self.lock:
            self.photos_dir.mkdir(parents=True, exist_ok=True)
            path = self.photos_dir / f"{stem}.jpg"
            n = 2
            while path.exists():  # 같은 기기가 1초에 여러 장
                path = self.photos_dir / f"{stem}_{n}.jpg"
                n += 1
            path.write_bytes(data)
        return path.name

    def list(self) -> list[str]:
        if not self.photos_dir.exists():
            return []
        files = sorted(self.photos_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files]

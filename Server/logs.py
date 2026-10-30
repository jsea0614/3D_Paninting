"""기기별 로그 검증 및 크기 제한 회전 저장."""
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

MAX_BODY = 256 * 1024
MAX_ENTRIES = 200
MAX_FILE = 5 * 1024 * 1024
LEVELS = {"Log", "Warning", "Error", "Exception", "Assert"}
DEVICE_RE = r"^[a-z]+-[0-9a-f]{1,16}$"


def text(value, limit):
    # 한 항목이 다른 로그 줄의 헤더를 위조하지 않도록 줄바꿈을 접습니다.
    return str(value)[:limit].replace("\r", " ").replace("\n", " ")


class DeviceLogs:
    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.lock = threading.Lock()

    def append(self, batch: dict) -> None:
        if not isinstance(batch, dict):
            raise HTTPException(400, "로그 본문은 객체여야 합니다.")
        device = batch.get("device")
        if not isinstance(device, str) or re.fullmatch(DEVICE_RE, device) is None:
            raise HTTPException(400, "잘못된 기기 이름입니다.")
        entries = batch.get("entries")
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES or any(not isinstance(e, dict) for e in entries):
            raise HTTPException(400, "로그 항목은 최대 200개의 객체 목록이어야 합니다.")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        context = f"client {text(batch.get('clientId', -1), 32)}, char {text(batch.get('characterId', -1), 32)}, {text(batch.get('role', '?'), 32)} {text(batch.get('appVersion', ''), 64)}"
        lines = []
        dropped = batch.get("dropped", 0)
        if type(dropped) is int and dropped > 0:
            lines.append(f"{now} [Warning] (dropped {dropped})\n")
        for entry in entries:
            level = entry.get("level", "Log")
            if not isinstance(level, str) or level not in LEVELS:
                level = "Log"
            count = entry.get("count", 1)
            if type(count) is not int or count < 1:
                count = 1
            repeat = f" ×{count}" if count > 1 else ""
            lines.append(f"{text(entry.get('t', now), 64)} [{level}] ({context}) {text(entry.get('msg', ''), 2000)}{repeat}\n")
            stack = str(entry.get("stack", ""))[:4000]
            if stack:
                lines.extend(f"    {line}\n" for line in stack.splitlines())

        with self.lock:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            path = self.logs_dir / f"{device}.log"
            if path.exists() and path.stat().st_size >= MAX_FILE:
                path.replace(path.with_suffix(".log.1"))
            with path.open("a", encoding="utf-8") as stream:
                stream.writelines(lines)

    def list(self) -> list[dict]:
        with self.lock:
            result = []
            for path in sorted(self.logs_dir.glob("*.log")):
                stat = path.stat()
                result.append({"device": path.stem, "size": stat.st_size,
                               "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")})
            return result

# 운영 PC 서버

학생 PC 색칠 앱이 캐릭터(PNG + JSON)를, 운영자 폰이 사진 또는 스캔 파일을 올리고, Quest 앱이 같은 Wi-Fi에서 받아 가는 서버입니다. 기존 캐릭터·GLB 업로드는 인터넷 없이 동작하며 사진 생성은 설정된 GPU 작업기 연결이 필요합니다. 데이터 규약: [../Contract/CONTRACT.md](../Contract/CONTRACT.md)

운영 PC에서는 VR 저장소(`JobExperienceWorld`)의 Unity 메뉴 **Tools > Server > Server Control** 창으로 설치·실행·중지할 수도 있습니다 (이 폴더를 서버 폴더로 지정).

## 설치와 실행

1. [uv](https://docs.astral.sh/uv/)와 Blender(4.2 LTS 이상, 5.2 LTS에서 확인)를 설치합니다. Blender는 스캔 변환에만 씁니다.
2. 실행:
   ```
   cd Server
   uv sync
   uv run main.py --port 8081
   ```
   Blender는 환경 변수 `BLENDER` → `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` → PATH 순서로 찾고, `--blender <경로>`로 지정할 수도 있습니다. 시작 로그 첫 줄에 쓰는 Blender 경로가 나옵니다.

- 같은 네트워크의 기기는 `http://<운영 PC IP>:8081`으로 접속합니다. 처음 실행할 때 Windows 방화벽 창이 뜨면 **개인 네트워크**를 허용합니다.
- 데이터는 `Server/data/`에 저장됩니다(`--data-dir`로 변경). 행사를 새로 시작하려면 서버를 끄고 이 폴더를 지웁니다.
- 운영자 폰·iPad는 `http://<운영 PC IP>:8081/scan`에서 장식 이름(1~12자)과 사진을 보내고 후보를 고릅니다. 직접 스캔한 파일은 접힌 "GLB 파일로 올리기"로 보낼 수 있습니다. 모든 학생이 쓰는 공유 라이브러리에 등록됩니다 (홈 화면에 추가해 두면 편함).
- 스캔을 지울 때는 **서버 PC**에서 `http://127.0.0.1:8081/scan`을 엽니다. 목록의 [지우기]로 GLB·썸네일을 함께 지웁니다. 다른 기기에서는 삭제할 수 없습니다. 등록된 스캔과 대기·생성·변환·후보 선택 대기는 합쳐 최대 40개이며, 가득 차면 안 쓰는 스캔을 지운 뒤 보냅니다.
- 학생 PC 색칠 앱 설치: 운영 PC에서 `Tools/Build/Paint App (Windows)`로 `Builds/PaintApp`을 만든 뒤(`--paintapp-dir`로 변경), 학생 PC 브라우저로 `http://<운영 PC IP>:8081/download`를 열고 PC 번호를 누릅니다. 받은 zip의 `config.json`에 그 번호와 접속한 서버 주소가 들어 있어 압축만 풀고 실행하면 됩니다. zip은 `data/dist/`에 캐시되고 빌드가 바뀌면 다시 만듭니다 (첫 요청은 압축하느라 수십 초 걸릴 수 있음).

## API

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| POST | `/api/character` | multipart: `id`, `json`(규약 JSON 문자열), `file`(PNG 1024 또는 2048). 규약대로 정리해 저장하고 `version`이 1 오름. 응답의 `warnings`에 버린 장식 |
| GET | `/api/characters` | `{"characters": [{id, name, version, updatedAt, png, json}]}`. `png`·`json` URL에 `?v={version}`이 붙어 Quest가 새 버전을 알 수 있음 |
| GET | `/characters/{id}.png`, `/characters/{id}.json` | 최신 파일 |
| POST | `/api/scans` | multipart: `name`(필수, 앞뒤 공백 제거 후 1~12자), `file`(.usdz .usd .usdc .usda .glb .gltf .obj .fbx, 200MB 이하) → 202와 변환 작업. 잘못된 이름은 400, 라이브러리 상한은 409 |
| POST | `/api/scans/photo` | multipart: `name`(1~12자), `image`(JPG/PNG, 20MB 이하) → 202 `{"jobId"}`. 미설정 503, 상한 409 |
| GET | `/api/scans/worker` | 작업기 상태 `{"configured", "ok", "loaded", "queue"}` (토큰은 반환하지 않음) |
| GET | `/api/scans/jobs/{jobId}` | `status`: `queued` → `converting` → `done`(`itemId`, `file`) 또는 `failed`(`error`) |
| GET | `/api/scans/jobs/{jobId}/candidates/{k}.png` | 선택 대기 중인 유효 후보의 썸네일. 없는 후보·선택/버리기/만료 후 404 |
| POST | `/api/scans/jobs/{jobId}/choose` | JSON `{"index": k}` → 등록 항목(사진은 `seed` 포함). 상태·후보가 유효하지 않거나 중복 선택이면 409, 없는 작업 404 |
| POST | `/api/scans/jobs/{jobId}/discard` | 선택 대기 후보를 버리고 작업 폴더 삭제. 완료·진행 중 작업은 409 |
| GET | `/api/scans` | `{"scans": [...], "max": 40, "canManage": bool}`. 항목: `itemId`, `source`, `file`, `thumb`(없으면 null), `name`, `createdAt`, `triangles`, `bytes`, `textureSize`, `dimensions`, `cleaning`. `canManage`는 요청 IP가 `127.0.0.1` 또는 `::1`인 경우만 true |
| DELETE | `/api/scans/{itemId}` | 서버 PC에서만 삭제 (그 외 403), 없으면 404, 성공 204 |
| GET | `/scans/{파일}.glb`, `/scans/{파일}.png` | GLB(`model/gltf-binary`)·썸네일(`image/png`) |
| GET | `/scan` | iPad용 스캔 업로드 페이지 |
| GET | `/api/health` | 연결 확인 |
| POST | `/api/logs` | 기기 로그 JSON (최대 256KB·200개). `data/logs/{device}.log`에 저장, 5MB에서 `.log.1`로 회전 |
| GET | `/api/logs` | 로그 파일 목록: `devices`의 `device`, `size`, `updatedAt`(UTC) |
| POST | `/api/photos` | Quest 손 카메라 사진 multipart: `file`(JPEG, 4MB 이하), `device`(로그와 같은 형식), `characterId`(없으면 −1). `data/photos/{07번\|번호없음}_{YYYYMMDD-HHMMSS}_{device}.jpg` 저장 |
| GET | `/api/photos` | 사진 파일 이름 목록 `photos` (최신 먼저) |
| GET | `/download` | 학생 PC용 색칠 앱 받기 페이지 (PC 번호 버튼) |
| GET | `/api/paintapp` | 색칠 앱 빌드 정보: `available`, `sizeMB`, `builtAt` |
| GET | `/download/PaintApp_{n}.zip` | PC 번호 n(1~99)용 색칠 앱. `config.json`의 `studentId`=n, `serverUrl`=요청한 주소. 빌드가 없으면 404 |

이전 API(`POST /api/texture`, `GET /textures/{id}.png`, `GET /api/players`)는 색칠 앱이 `/api/character`로 옮겼으므로 지울 예정입니다 (`load_test.py`를 새 API로 바꾼 뒤).

### 사진 → AI 3D 운영

GPU PC 준비는 [worker/README.md](worker/README.md)를 따릅니다. 작업기 주소와 같은 토큰을 **서버 실행 프로세스** 환경 변수로 넣고 서버를 재시작합니다 (PowerShell):

```powershell
$env:TRELLIS_WORKER_URL = 'http://<GPU-PC-Tailscale-IP>:8090'
$env:TRELLIS_WORKER_TOKEN = '<작업기와 같은 공유 비밀>'
uv run main.py --port 8081
```

두 값 중 하나라도 비어 있으면 사진 업로드만 503 `"3D 생성 PC가 설정되지 않았어요"`입니다. Unity `Server Control`로 띄울 때는 Unity가 이 환경 변수를 상속하도록 설정 후 Unity를 다시 실행합니다. 토큰을 저장소·사진 페이지·로그에 넣지 않습니다.

사진 작업은 `kind: "photo"`, `status`: `queued` → `generating`(`progress: {done, total}`) → `converting` → `choose` → `done` 또는 `discarded`입니다. 어느 단계든 실패하면 `failed`와 `error`를 확인합니다. `candidates`는 `index`(0부터), `ok`, `thumb`(유효한 후보 URL), `triangles`, `error`를 가집니다. 시드가 실패하거나 변환·검사를 통과하지 못한 후보는 `ok: false`이고 고를 수 없습니다. 모두 실패하면 재촬영 안내가 나옵니다. 사진 후보는 선택에 미리보기가 필요하므로 썸네일 실패도 해당 후보 실패입니다 (기존 GLB 경로의 썸네일 선택 사항은 유지).

시드 3개로 생성하며 작업기 상태는 5초마다 확인하고 원격 생성·수신 전체를 10분으로 제한합니다. 10분은 작업기가 그 사진을 실제로 시작한 때부터 세므로(작업기는 한 번에 한 장, 약 7분), 여러 장을 올리면 앞 사진을 기다리는 동안은 시간 초과가 나지 않습니다. 대기는 최대 1시간입니다. 생성 중에는 기존 GLB 변환이 계속 진행됩니다. 후보 변환은 기존 Blender 대기열에서 하나씩 실행하며 선택 전에는 목록에 등록하지 않습니다. `choose`는 한 번만 성공하고 사진 이름·원본 파일 이름·선택한 `seed`를 저장합니다. 후보는 30분 후 자동으로 버립니다 (유휴 대기열 정리는 최대 약 5초 간격). 다시 만들기는 이전 후보를 버린 후 페이지가 가지고 있는 같은 사진을 다시 보냅니다. 페이지를 새로 열면 사진을 다시 골라야 하고 이전 후보는 만료됩니다.

사진은 정면보다 20~30° 위에서, 물건이 화면 절반 이상 차도록 단색 배경에서 찍습니다. 실제 생성 시간·품질·색은 GPU와 사진에 따라 달라집니다. 2026-11-10 기존 WSL/CUDA 환경과 RTX 4070 Ti SUPER에서 실제 사진 → 후보 3개 → Blender 정리까지 410.26초를 측정했습니다 (다른 Unity 작업 병행). 선택 결과는 1,410삼각형·512²·60,456B였고, 폰 등록·색칠 앱 목록·Quest 표시/색/방향을 확인했습니다. 검증용 작은 장식이 머리 안에 들어가 보이지 않았던 배치는 Head pos.y=0.45로 올려 확인했으며, 모든 장식에 같은 위치를 강제하지 않습니다. TRELLIS의 사진 정면(glTF +Z)을 변환에서 추가 회전하지 않습니다. 이전에 등록한 스캔은 새 기준으로 자동 재변환하지 않습니다.

### 검사 규칙

- 캐릭터: `id`·`name`·`texture`가 규약에 맞지 않으면 400으로 거절합니다. 장식 문제는 그 장식만 버리고 저장하며 `warnings`로 알려 줍니다 (`contract.py`, C#과 같은 규칙).
- 장식 ID와 본 목록은 Unity가 내보낸 `Contract/item_ids.json`을 씁니다.
- 스캔 장식은 모든 학생이 공유하며 서버에 등록된 스캔이면 누구나 참조할 수 있습니다. 미등록 스캔은 버립니다 (`UnknownScan`, 서버에만 있는 규칙). 스캔을 지워도 기존 캐릭터 JSON은 바꾸지 않으며 다음 업로드 때 해당 장식을 버립니다. 앱에서 GLB를 못 받으면 그 장식만 건너뜁니다.
- 스캔 변환: `tools/convert_scan.py`(Blender)가 겹친 정점·미세 면·작은 조각·넓은 바닥 판을 청소하고 작은 구멍을 메운 뒤 감량·텍스처 1장·크기 정규화를 합니다. 서버가 결과 GLB를 `glb.py`로 다시 검사합니다 (삼각형 2,000개, 감량 목표 1,500개, 텍스처 1장 512px, 2MB, 최대 0.5m, 피벗 중심). 하나라도 어기면 등록하지 않습니다. 청소 후 면이 200개 미만이거나 가장 긴 변이 1cm 미만이어도 실패합니다. 변환은 한 번에 하나씩 처리합니다.
- 256×256 투명 PNG 썸네일도 생성합니다. 썸네일만 실패하면 GLB는 등록하고 `thumb`는 null입니다. 청소 결과는 `cleaning`의 `removedIslands`·`floorRemoved`·`holesFilled`로 확인합니다. 기존 `owner` 항목만 있는 색인은 이름을 "스캔", 썸네일을 null로 읽습니다.

## 테스트

```
uv run pytest                     # Blender가 설치돼 있으면 실제 변환 시험도 함께 실행
uv run load_test.py               # 학생 20명 업로드 + Quest 20대 다운로드 부하 시험 (이전 API 기준)
```

변환 파이프라인을 따로 시험할 때:

```
blender -b --factory-startup -P tools/make_test_scans.py -- <폴더>      # 시험용 스캔 4종
blender -b --factory-startup -P tools/convert_scan.py -- <입력> <출력.glb> <보고서.json> <썸네일.png>
```

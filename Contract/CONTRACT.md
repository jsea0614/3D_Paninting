# 캐릭터 데이터 규약 (페인팅 툴 ↔ Quest 앱)

색칠 앱(이 저장소)과 Quest VR 앱(저장소 `JobExperienceWorld`)이 함께 지키는 규약의 정본입니다. 바뀌면 이 파일과 `samples/`, 양쪽 검증 코드(C# 공유 패키지, `Server/contract.py`)를 함께 고치고, VR 앱과 색칠 앱 모두에서 확인합니다.

- C# 기준 구현: `Packages/com.wonju.character-shared/Runtime/Contract/` (`CharacterData`, `CharacterContract`)
- Python 기준 구현: `Server/contract.py`
- 샘플과 예상 결과: `samples/`, `samples/expected.json`

## 캐릭터 1명 = `{id}.png` + `{id}.json`

### 텍스처 `{id}.png`
- 모든 캐릭터가 같은 모델, 같은 UV 레이아웃을 쓴다.
- 1024×1024 (최대 2048×2048), PNG, sRGB.

### 정보 `{id}.json`
```json
{
  "id": 7,
  "name": "민수",
  "texture": "7.png",
  "accessories": [
    { "itemId": "hat_01", "bone": "Head", "pos": [0, 0.12, 0], "rot": [0, 0, 0], "scale": 1.0 },
    { "itemId": "scan_ab12", "source": "scan", "file": "scans/ab12.glb",
      "bone": "Head", "pos": [0, 0.15, 0], "rot": [0, 0, 0], "scale": 1.0 }
  ]
}
```

| 필드 | 규칙 |
| --- | --- |
| `id` | 1 이상의 정수. 학생 PC 번호 = Quest 번호. 파일 이름은 앞자리 0 없이 `7.png` |
| `name` | 별명, 1~8자. VR에서 다른 학생에게 보임 |
| `texture` | `{id}.png` |
| `accessories[].itemId` | 기본 장식은 `AccessoryDatabase`의 ID, 스캔 장식은 `scan_` + 서버가 만든 ID |
| `accessories[].source` | `"builtin"`(생략 가능) 또는 `"scan"` |
| `accessories[].file` | 스캔 장식만. `scans/{파일}.glb` |
| `accessories[].bone` | 캐릭터 리그의 Transform 이름 그대로 |
| `accessories[].pos` | 본 기준 로컬 위치(m), 숫자 3개 |
| `accessories[].rot` | 본 기준 로컬 오일러 각도(도), 숫자 3개. Unity `Quaternion.Euler(x, y, z)`로 해석 |
| `accessories[].scale` | 균일 스케일, 0보다 큼 |

### 개수 제한과 잘못된 값

받는 쪽은 아래 순서로 처리하고, 버린 항목마다 경고 로그만 남긴다 (크래시 없음).

1. 잘못된 항목을 버린다: 알 수 없는 `itemId`·`bone`, 숫자 3개가 아닌 `pos`·`rot`, 0 이하 `scale`, `scans/*.glb`가 아닌 스캔 `file`
2. 남은 항목 중 스캔 장식은 앞에서부터 **2개**까지만 남긴다.
3. 남은 항목 전체를 앞에서부터 **5개**까지만 남긴다 (스캔 2개도 이 5개에 포함).

보내는 쪽(색칠 앱, 서버)은 같은 규칙으로 미리 검사해, 정상 데이터만 보내는 것을 목표로 한다.

스캔 장식은 모든 학생이 공유하며, 서버에 등록된 스캔이면 누구나 참조할 수 있다. 서버는 등록되지 않은 스캔을 가리키는 장식을 1단계에서 버린다 (`UnknownScan`). 스캔을 지워도 기존 캐릭터 파일은 고치지 않고 다음 업로드 때 버린다. 앱이 GLB를 못 받으면 그 장식만 건너뛴다.

스캔 GLB는 청소·변환 후 경량화 기준을 서버가 다시 검사해 통과한 것만 `scans/`에 등록한다: 삼각형 **2,000개 이하**(감량 목표 1,500개), 텍스처 **1장, 512×512 이하**, GLB **2MB 이하**, 단위 m, 피벗은 바운딩박스 중심, 최대 길이 **0.5m 이하**. 공유 라이브러리는 등록된 스캔과 대기·생성·변환·후보 선택 대기 작업을 합쳐 최대 40개이며, 썸네일은 256×256 투명 PNG(기존 GLB 업로드는 실패하면 없음)다.

사진 생성은 `POST /api/scans/photo`에 이름과 JPG/PNG 사진 1장(20MB 이하)을 보내 접수한다. 작업기가 시드 3개로 만든 결과를 각각 같은 변환·검사에 통과시킨 후, `/api/scans/jobs/{jobId}/choose`에 후보 `index`를 보내야 등록된다. 선택 전 후보는 학생 목록에 없다. 후보 선택은 30분 안에 해야 하며 버리기 또는 만료 시 예약 자리와 파일을 비운다. 사진 후보는 눈으로 고르는 데 필요하므로 썸네일 생성 실패도 해당 후보의 실패로 처리한다. TRELLIS의 사진 정면은 glTF +Z이며 변환 단계에서 추가 회전하지 않는다. 캐릭터 JSON·스캔 ID·`ScanStore` 형식은 그대로다 (사진 생성 항목에는 재현용 `seed`만 추가).

## 결정 사항

| 질문 | 결정 |
| --- | --- |
| 장식은 어디서 붙이나 | PC 색칠 앱에서 붙이고 Quest는 읽기만 |
| 서버 주소 | 포트 8081. 개발 PC `http://127.0.0.1:8081`, 행사 운영 PC `http://192.168.0.10:8081` |
| 서버 경로 | `POST /api/character` 업로드, `GET /api/characters` 목록, `/characters/{id}.png`, `/characters/{id}.json`, `/scans/{file}`. 자세한 API는 `Server/README.md` |
| 다시 보낸 캐릭터 | 목록의 `version`이 오르면 Quest가 다시 받음. Quest는 5초마다 목록 확인 (VR 앱 구현됨) |
| 장식 모델·ID 목록 | 공유 패키지 `com.wonju.character-shared`의 `AccessoryDatabase`를 두 앱이 함께 사용 (VR 앱은 `file:` 경로로 이 패키지를 참조) |
| 붙일 수 있는 본 | `Head`, `Chest`, `Hips`, `Left_Hand_Socket`, `Right_Hand_Socket` |
| 캐릭터 크기 | VR에서는 루트 스케일로 키우고, 본 기준 값은 그대로 (VR 앱 `AvatarBody`가 머리 높이에 맞춰 조절) |
| 별명 | 색칠 앱 보내기 창에서 입력 (1~8자), 비우면 `{id}번` |

## 기본 장식 ID

`Packages/com.wonju.character-shared/Runtime/AccessoryDatabase.asset`이 정본이고, 서버 검사용 사본은 `item_ids.json`입니다 (Unity 메뉴 Tools > Character > Export Accessory IDs).
지금 프리팹은 기본 도형으로 만든 **임시 모델**입니다. 실제 모델로 바꿀 때 `itemId`는 그대로 둡니다.

| itemId | 이름 | 붙일 수 있는 본 (첫 번째가 처음 붙는 본) |
| --- | --- | --- |
| `hat_01` | 모자 | `Head` |
| `glasses_01` | 안경 | `Head` |
| `backpack_01` | 가방 | `Chest`, `Hips` |
| `crown_01` | 왕관 | `Head` |
| `wand_01` | 요술봉 | `Right_Hand_Socket`, `Left_Hand_Socket` |

프리팹 규칙: 루트 원점이 본에 붙는 지점, 렌더러 1개(재질별 서브메쉬), 콜라이더 없음, 셰이더 `Wonju/CharacterPaint`.

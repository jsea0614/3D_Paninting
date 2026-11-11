# 3D 색칠하기 (원주시 직업체험 PC 색칠 앱)

학생이 PC에서 3D 캐릭터를 마우스로 돌려 가며 칠하고, 완료하면 운영 PC 서버로 캐릭터(텍스처 PNG + 별명·장식 JSON)를 보냅니다. Quest 앱은 같은 서버에서 받아 아바타에 입힙니다.

Quest VR 앱은 같은 담당자의 저장소 `JobExperienceWorld`이며, 두 저장소를 같은 폴더에 나란히 둡니다 (VR 앱이 `Packages/com.wonju.character-shared`를 `file:` 경로로 참조하고, VR 저장소의 서버 컨트롤 창이 `Server/`를 실행).

| 위치 | 내용 |
| --- | --- |
| `Assets/_Project` | 색칠 앱 (씬 `Scenes/PaintApp.unity`) |
| `Packages/com.wonju.character-shared` | Quest 앱과 같이 쓰는 캐릭터, `Wonju/CharacterPaint` 셰이더, 부위 마스크 |
| `Server/` | 운영 PC 업로드 서버 (Python, [사용법](Server/README.md)) — 휴대폰 사진 → 스캔 장식 페이지 `/scan` 포함 |
| `Server/worker/` | 사진 → 3D AI(TRELLIS.2) GPU 작업기, GPU PC WSL에서 따로 실행 ([설치](Server/worker/README.md)) |
| `Art/Icons/` | UI 아이콘 원본과 생성 스크립트 |

## 개발

- Unity 6000.3.23f1 (URP), 에디터에서 `PaintApp` 씬을 열고 Play
- 프로젝트 폴더의 `config.json`(git 제외)을 `config.example.json`에서 복사해 `serverUrl`, `studentId`를 정합니다.
- 서버: `cd Server && uv run main.py`, 테스트: `uv run pytest`

## 배포 (학생용 PC)

1. Unity 메뉴 **Tools > Build > Paint App (Windows)** → `Builds/PaintApp/`
2. 폴더째 각 PC에 복사하고 `config.json`의 `studentId`를 Quest 번호와 같게 바꿉니다. 자세한 내용은 빌드 폴더의 `사용법.txt`
3. 운영자 단축키: `Ctrl+Shift+R` 회차 초기화, `Ctrl+Shift+Q` 종료
4. 행사 전 부하 확인: 운영 PC에서 서버를 켜고 `cd Server && uv run load_test.py --server http://<운영 PC IP>:8081`

## 장식 조절

- **꾸미기**에서 장식을 선택하면 이동 화살표가 나타납니다. 빨강 X·초록 Y·파랑 Z 축을 끌어 이동합니다. 시선과 나란한 축은 카메라를 돌려 조절합니다.
- **이동 (W) / 회전 (E) / 크기 (R)** 버튼 또는 단축키로 전환합니다. 고리를 끌어 회전하고, 가운데 흰 사각형을 오른쪽·위로 끌면 커집니다. 크기는 처음 달았을 때의 0.5~2배입니다.
- 축은 캐릭터 기준이며, 축 이동은 달린 본을 유지합니다. 다른 본에 달려면 장식 몸체를 캐릭터 표면으로 끈 뒤 축으로 세밀하게 조절합니다.
- **붙일 부위**의 머리·몸통·허리·왼손·오른손으로 따라갈 부위를 직접 지정할 수 있습니다. 현재 부위를 강조하고, 장식이 허용하지 않는 부위는 누를 수 없습니다. 보이는 위치·방향은 유지됩니다. 머리 위로 옮긴 토끼가 팔을 따라가면 **머리**를 선택한 뒤 보내세요.
- 끄는 중 **Esc**는 취소, **Ctrl+Z / Ctrl+Y**는 되돌리기·다시하기입니다. 드래그 한 번은 기록 한 단계이며, 기존 회전·크기·처음 자리 버튼도 사용할 수 있습니다.
- 기즈모를 끄는 동안 카메라는 멈춥니다. 색칠 모드·완료 창·미선택 상태에서는 기즈모가 숨겨집니다.

## 실제 베이스 캐릭터로 바꾸기

색칠 앱 코드는 바꾸지 않고 공유 패키지만 바꿉니다.

1. 새 FBX를 `Packages/com.wonju.character-shared/Runtime/Models/character_vr_paint3d.fbx`에 덮어씁니다 (파일 이름이 다르면 `CharacterAssetBaker.ModelPath`를 고칩니다).
2. 메뉴 **Tools > Character > Bake Part Mask and Default Texture** 실행 → 콘솔 보고서 확인
   - `겹침`이 0%에 가까워야 합니다. 겹치면 한 곳을 칠할 때 다른 곳도 칠해지므로 모델의 UV를 고쳐야 합니다.
   - 부위별 면적이 0%인 부위가 있으면 본 이름이 `CharacterParts.FromBoneName` 규칙과 맞는지 확인합니다.
   - `Temp/CharacterBake/uv_overlap.png`에서 UV 배치를 눈으로 확인합니다 (빨강 = 겹침).
3. `Runtime/Prefabs/Character.prefab`을 열어 머티리얼이 `CharacterPaint`인지 확인하고 Play로 칠해 봅니다.
4. 캐릭터가 +Z를 보고 서 있지 않으면 FBX 임포트 설정이나 프리팹 회전을 고칩니다.

## 사용성 시험 체크리스트 (학생 또래 3~5명)

시작 전 1분 시연만 하고, 진행자는 도와주지 않고 관찰합니다.

- [ ] 20분 안에 색칠을 마치고 [완료] → [보내기]까지 갔나요?
- [ ] 캐릭터를 돌리는 방법(우클릭 또는 배경 드래그)을 스스로 찾았나요?
- [ ] 부위 채우기와 붓의 차이를 이해했나요?
- [ ] 되돌리기를 써야 할 때 찾았나요?
- [ ] 얼굴처럼 작은 부분을 휠로 확대해 칠했나요?
- [ ] 막힌 곳, 다시 물어본 것, 잘못 누른 버튼을 기록합니다.

# TRELLIS.2 GPU 작업기

GPU PC의 WSL `Ubuntu-24.04`, `~/TRELLIS.2/.venv`에서 실행합니다. 운영 서버는 GPU·torch가 필요 없고 표준 `urllib`로 이 작업기에만 요청합니다. `Server/pyproject.toml`에 생성 의존성을 넣지 않습니다.

아래는 지시서에 기록된 2026-11-05 RTX 4070 Ti SUPER 설치 절차를 정리한 것입니다. 2026-11-10 기존 환경에서 실제 GPU 생성·Windows localhost 연결·폰 등록·색칠 앱 목록·Quest 표시/색/방향을 확인했습니다. WSL 재설치와 별도 PC Tailscale 경유는 수행하지 않았습니다. 설치 명령은 해당 시험 환경의 재현 절차입니다.

## WSL 환경 준비

Windows에서 `wsl -d Ubuntu-24.04`로 들어갑니다 (기본 배포판과 다를 수 있음). 다음은 WSL 셸에서 실행합니다.

```bash
sudo apt update
sudo apt install -y build-essential ninja-build libjpeg-dev zlib1g-dev python3.12-venv python3-dev wget
```

CUDA 12.4는 NVIDIA의 **WSL 전용** 저장소를 사용합니다. WSL에는 Linux 디스플레이 드라이버를 설치하지 않습니다.

```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt update
sudo apt install -y cuda-compiler-12-4 cuda-libraries-dev-12-4 cuda-cudart-dev-12-4 cuda-nvtx-12-4
```

시험 환경에서 `cuda-toolkit-12-4` 묶음은 Ubuntu 24.04의 `nsight-systems` → `libtinfo5` 의존성 때문에 실패해 필요한 패키지를 위처럼 개별 설치했습니다.

## TRELLIS와 모델

```bash
git clone --recursive https://github.com/microsoft/TRELLIS.2 ~/TRELLIS.2
cd ~/TRELLIS.2
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
pip install imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja trimesh gradio==6.0.1 tensorboard pandas lpips zstandard
pip install 'transformers==4.57.3' pillow kornia timm
pip install git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8
```

기본 패키지는 TRELLIS `setup.sh --basic` 목록을 따르되 `pillow-simd` 대신 `pillow`를 사용합니다. 시험 당시 최신 transformers에서 DINOv3 `layer` 속성 오류가 있어 `4.57.3`으로 고정했습니다.

```bash
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"
export TORCH_CUDA_ARCH_LIST=8.9
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
bash setup.sh --nvdiffrast --cumesh --flexgemm --o-voxel
pip install flash-attn==2.7.3 --no-build-isolation
pip install fastapi uvicorn python-multipart
```

`8.9`는 RTX 4070 Ti SUPER 기준입니다. 다른 GPU는 그 장비의 CUDA 아키텍처로 바꿉니다. nvdiffrast는 `v0.4.0`, CuMesh·FlexGEMM·o-voxel은 TRELLIS 설정 스크립트를 사용합니다. nvdiffrec은 미리보기 영상용이라 설치하지 않습니다. 재현을 위해 실제로 사용한 TRELLIS·확장 커밋은 현장 설치 후 기록해 둡니다.

Hugging Face에서 `facebook/dinov3-vitl16-pretrain-lvd1689m` 사용 승인을 받은 Read 토큰으로 `hf auth login`을 실행합니다. 모델은 `microsoft/TRELLIS.2-4B`, `microsoft/TRELLIS-image-large`(ss_dec), `ZhengPeng7/BiRefNet`이며 지시서의 다운로드 예상치는 약 16GB입니다. 첫 준비에는 인터넷이 필요하고 이후 캐시를 사용합니다.

## 실행과 API

```bash
source ~/TRELLIS.2/.venv/bin/activate
python /mnt/d/Unity/3D_Paninting/Server/worker/trellis_worker.py --host 0.0.0.0 --port 8090 --token '<공유 비밀>'
```

저장소 경로가 다르면 스크립트 경로를 바꿉니다. `--trellis ~/TRELLIS.2`가 기본값입니다. 토큰은 충분히 긴 임의 문자열을 사용하고 운영 서버의 `TRELLIS_WORKER_TOKEN`과 맞춥니다.

모델은 시작할 때 별도 스레드에서 한 번 로드됩니다. `/health`는 로드 전 `loaded: false`, 준비 후 `loaded: true`입니다. 로드 실패는 `ok: false`와 작업기 콘솔로 확인합니다. 모든 경로(health·docs·파일 포함)에 `X-Worker-Token`이 필요하며 틀리면 401입니다.

| 메서드 | 경로 | 내용 |
|---|---|---|
| GET | `/health` | `{ok, loaded, queue}`. `queue`는 실행 중을 제외한 대기 수 |
| POST | `/jobs` | multipart `image`(JPG/PNG, 20MB 이하), `seeds`(0~2147483647 쉼표 구분, 1~4개), `faces`(기본 1500, 1~2000) → 202 `{jobId}` |
| GET | `/jobs/{id}` | `{status: queued/running/done/failed, done, total, error, candidates: [bool, ...]}`. `done`은 성공·실패를 합친 시도 수 |
| GET | `/jobs/{id}/{k}.glb` | 0부터 시작하는 성공 후보. 실패 후보는 404 |
| GET | `/jobs/{id}/cut.png` | 배경 제거 결과 |

한 스레드가 한 번에 한 작업·한 시드씩 처리합니다. BiRefNet을 `ZhengPeng7/BiRefNet` + `.float()`로 바꾸고 EXIF 방향을 보정한 RGB를 사용합니다. 각 시드마다 `1024_cascade` 전체를 실행하고 `mesh.simplify(16777216)` 후 `to_glb`(1500면 기본, 텍스처 1024, remesh=False) → PNG 텍스처 GLB로 내보냅니다. 최종 512px·2MB·2000면 검사와 정규화는 운영 서버의 Blender 단계에서 합니다. 한 시드의 CUDA OOM은 캐시를 비우고 다음 시드를 계속합니다.

결과는 `~/scanlab/jobs/{id}/`에 보관하고 완료 후 1시간이면 지웁니다. 재시작 후 오래된 UUID 작업 폴더도 정리합니다. 진행 중 작업은 재시작으로 사라지고 서버가 실패로 표시합니다. 업로드 사진은 모든 시드 시도 후 지웁니다.

## Windows·Tailscale 연결 (현장 확인 필요)

먼저 GPU PC Windows에서 다음을 확인합니다:

```powershell
curl.exe -H 'X-Worker-Token: <공유 비밀>' http://localhost:8090/health
```

Windows 호스트의 localhost 연결과 다른 PC의 Tailscale 연결은 별개입니다. 서버 PC와 GPU PC 모두 Tailscale에 연결한 뒤 서버의 `TRELLIS_WORKER_URL`에는 GPU PC의 Tailscale IP를 넣습니다. 이번 PC에서는 별도 portproxy 설정 없이 Windows의 localhost:8090으로 WSL 작업기에 연결됐습니다. 같은 PC에서 운영 서버도 실행할 때는 TRELLIS_WORKER_URL=http://127.0.0.1:8090을 사용할 수 있습니다. 다른 PC에서 들어오는 Tailscale 전달은 미확인입니다. 별도 PC 운영 시 아래 방식 중 실제 연결 시험을 통과한 것을 사용합니다.

- Windows 11 22H2 이상에서는 `%UserProfile%/.wslconfig`의 `[wsl2]` 아래 `networkingMode=mirrored`를 검토합니다. 기존 설정을 보존하고 WSL을 다시 시작한 뒤 연결을 확인합니다. Hyper-V 방화벽도 WSL 수신에 영향을 줍니다.
- NAT에서는 Windows 관리자 PowerShell의 `netsh interface portproxy`로 **GPU PC Tailscale IP:8090**을 WSL IP:8090에 전달할 수 있습니다. `wsl -d Ubuntu-24.04 hostname -I`로 IPv4를 확인하고, 재시작 때 WSL IP가 바뀌면 갱신합니다. 방화벽은 서버 PC의 Tailscale 주소에서 오는 TCP 8090만 허용하도록 현장에서 설정합니다.

명령 예시(실제 주소로 치환, 여기서는 실행하지 않음):

```powershell
netsh interface portproxy add v4tov4 listenaddress=<GPU-PC-Tailscale-IP> listenport=8090 connectaddress=<WSL-IPv4> connectport=8090
```

서버 PC에서 같은 토큰으로 `http://<GPU-PC-Tailscale-IP>:8090/health`가 되는지 확인해야 연결이 확정됩니다. 공용 인터넷에 포트를 직접 개방하지 않습니다.

## 인수 확인

- Windows localhost·Tailscale 경유 각각 health 확인
- 서버 설정 후 폰 `/scan`: 사진 → 후보 3개 → 선택 → 색칠 앱 스캔 탭
- 실제 소요 시간, 한 시드 실패 후 나머지 생성, 새로 시작 후 정리 확인
- Quest 장식 방향·크기·색 확인 (기존 시험의 어두운 썸네일 원인은 실기기로 분리 확인)

설치 참고: [TRELLIS.2 공식 저장소](https://github.com/microsoft/TRELLIS.2), [공식 setup.sh](https://github.com/microsoft/TRELLIS.2/blob/main/setup.sh), [Microsoft WSL 네트워크 문서](https://learn.microsoft.com/en-us/windows/wsl/networking).


## 2026-11-10 검증 기록

- Python 3.12.3 / torch 2.6.0+cu124 / CUDA 12.4 / RTX 4070 Ti SUPER, 기존 모델 캐시 사용.
- 사진 1장 → 시드 3개 → 후보 3개 변환 통과: 410.26초 (Unity 작업 병행).
- 선택한 토끼: 1,410삼각형, 512×512 텍스처, 60,456B. 폰에서 다른 사진 등록도 확인.
- 색칠 앱에서 두 항목과 썸네일, Quest 3에서 토끼 표시·색·정면 방향을 사용자 확인.
- 별도 PC/Tailscale·다인 부하·Quest 프레임 측정은 미실시.

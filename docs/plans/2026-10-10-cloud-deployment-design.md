# SPIDER 지휘소 클라우드 배포 설계안

작성 2026-10-10. 상태: **검토 대기** (구현 전).
대상 저장소: https://github.com/bckim0123-lab/d4d_2

## 1. 목표와 비목표

목표
- 임무 계획기(시나리오 → 계획 스트림 → 라이브러리 → 승인 패키지)를 URL 하나로 팀원에게 공개한다.
- 로컬 PC 없이도 브라우저에서 시나리오 작성, 계획 실행, 드릴 재생, 카드 생성을 할 수 있게 한다.
- 실기기 제어 패널은 "클라우드에서는 불가"를 명확히 표시하고, 로컬 실행 경로는 그대로 유지한다.

비목표
- 클라우드에서 FC USB(COM9)에 접근하거나 RAM 마스크를 변경하는 것. 물리적으로 불가능하며 시도하지 않는다.
- 아밍, 스로틀, 모터 시험을 어떤 배포 형태에서도 추가하는 것(PRD 비목표 유지).
- 다중 테넌트, 사용자별 권한, 과금. 팀 내부용 단일 인스턴스를 전제한다.

## 2. 현재 구조에서 배포에 영향을 주는 사실

| 사실 | 근거 | 배포 영향 |
|------|------|-----------|
| FastAPI가 `web/dist`를 `/`에 직접 서빙 | `src/d4d_web/api.py` 541~543행 | 프런트와 백엔드를 **한 프로세스**로 배포 가능. CORS·API 주소 설정 불필요 |
| 프런트는 상대 경로 `/api/...`만 호출 | `App.tsx`, `HardwareControlBay.tsx`의 `fetch` | 동일 출처 배포면 코드 수정 없음. 분리 배포면 API base URL 주입 필요 |
| 상태는 전부 파일시스템 | `library.py`: `ROOT/artifacts`, `ROOT/scenarios` | 서버리스(Vercel Functions, Lambda) 부적합. **영속 디스크** 필요 |
| 계획 실행은 SSE 장시간 스트림 + 스레드 | `POST /api/plan/stream`, `asyncio.to_thread(run_agent)` | 요청 시간 제한이 있는 서버리스 부적합. 상시 프로세스 필요 |
| 카드 생성에 Pillow, LLM에 openai/boto3 | `pyproject.toml` | Python 3.12 컨테이너로 해결 |
| 하드웨어는 `pyserial`로 COM9 직접 접근 | `hardware.py` 78행 | 클라우드에서는 항상 실패. 명시적 "비활성" 모드 필요 |
| 인증 없음, CORS `*` | `api.py` 87~93행 | 공개 인터넷 노출 시 **인증 필수** |
| 설정은 환경 변수 | `D4D_LLM_PROVIDER`, `OPENAI_API_KEY`, `D4D_FC_PORT`, `D4D_HARDWARE_WRITES` 등 | 호스팅의 시크릿 저장소에 넣으면 됨 |

## 3. 옵션 비교

### A. 단일 컨테이너 (추천)

FastAPI + 빌드된 프런트를 Docker 이미지 하나로 묶어 상시 실행 호스팅에 올린다.
후보: Fly.io, Railway, Render, Google Cloud Run(최소 인스턴스 1 + 영속 볼륨 대신 GCS).

- 장점: 코드 변경 최소(Dockerfile + 환경 변수), SSE 그대로 동작, CORS 문제 없음, 운영 단위 하나.
- 단점: 상시 비용(월 5~10달러 수준), 디스크 백업을 직접 챙겨야 함.
- 하드웨어: `D4D_HARDWARE_MODE=disabled`로 응답을 고정하고 UI에 "로컬 실행 전용" 안내.

### B. Vercel(프런트) + 별도 백엔드

- 장점: 프런트 배포가 쉽고 CDN이 붙는다.
- 단점: 백엔드는 어차피 A와 같은 상시 호스팅이 필요하다. 배포 두 개, 도메인 두 개, CORS 제한, 프런트에 API 주소 주입(`VITE_API_BASE`), SSE가 교차 출처로 흐른다. A 대비 추가 이득이 없다.
- 결론: 채택하지 않는다. 다만 A에서 정적 서빙 성능이 문제가 되면 그때 CDN만 앞에 둔다.

### C. 하이브리드 (A + 로컬 하드웨어 에이전트)

클라우드 UI를 쓰면서 실기기 제어만 로컬 PC의 API로 보내는 방식.
- HTTPS 페이지에서 `http://127.0.0.1:8765`를 호출하면 브라우저의 Private Network Access 정책에 걸릴 수 있다. Chrome은 localhost 예외를 두지만 버전마다 사전 요청(preflight) 요구가 다르다.
- 하드웨어 API 주소를 사용자가 UI에서 바꿀 수 있게 하면 공격면이 넓어진다.
- 결론: **1차 범위에서 제외**. 실기기 작업은 지금처럼 `scripts/dev.ps1` 로컬 실행으로 한다. 필요해지면 별도 설계.

## 4. 추천 아키텍처 (옵션 A) 상세

```
브라우저 ──HTTPS──▶ 호스팅 L7 프록시 ──▶ 컨테이너
                                        ├─ uvicorn d4d_web.api:app :8080
                                        │   ├─ /            → web/dist (정적)
                                        │   ├─ /api/...     → 계획·라이브러리·LLM
                                        │   └─ /api/hardware → 501 (disabled)
                                        └─ 볼륨 /data
                                            ├─ artifacts/   (실행 산출물)
                                            └─ scenarios/   (시나리오)
```

### 4.1 Dockerfile (다단계)

1. `node:24-alpine`에서 `web/` 빌드 → `web/dist`
2. `python:3.12-slim`에 `pip install .` + `dist` 복사
3. `CMD uvicorn d4d_web.api:app --host 0.0.0.0 --port 8080`
4. 헬스체크 `GET /api/health`

### 4.2 코드 변경 목록 (전부 소규모)

| # | 변경 | 파일 | 이유 |
|---|------|------|------|
| 1 | `D4D_DATA_DIR` 환경 변수로 `ARTIFACTS_DIR`/`SCENARIOS_DIR` 루트를 바꿀 수 있게 | `src/d4d_web/library.py` | 볼륨 마운트 경로(`/data`) 사용 |
| 2 | `D4D_HARDWARE_MODE=disabled`면 `/api/hardware/*`가 `501 {"code":"hardware_disabled"}` 반환 | `src/d4d_web/hardware.py`, `api.py` | COM 포트 탐색·pyserial 오류 대신 즉시 명확한 응답 |
| 3 | `GET /api/health`에 `hardware_mode`, `data_dir`, `version` 포함 | `api.py` | UI와 운영자가 배포 모드를 확인 |
| 4 | UI: health의 `hardware_mode === "disabled"`면 베이에 "실기기 제어는 로컬 실행 전용" 표시, FC 폴링·RAM 카드 숨김. BLE 연결·킬은 유지(HTTPS에서 Web Bluetooth 가능) | `web/src/HardwareControlBay.tsx` | 클라우드에서 "백엔드 연결 실패"가 반복 표시되는 것을 막음 |
| 5 | 인증: `D4D_AUTH_TOKEN`이 설정되면 `/api/*`에 `Authorization: Bearer` 요구. 로그인 화면은 토큰 입력 한 칸, `sessionStorage` 보관 | `api.py` 미들웨어, `App.tsx` | 공개 URL 보호. 없으면(로컬) 지금처럼 무인증 |
| 6 | CORS를 `D4D_CORS_ORIGINS`(기본 빈 값 = 동일 출처만)로 좁힘 | `api.py` | 동일 출처 배포라 `*` 불필요 |
| 7 | `Dockerfile`, `.dockerignore`, `docs/DEPLOY.md` | 신규 | 빌드·배포 절차 |

변경 1~3·5~6은 로컬 실행의 기본값을 바꾸지 않는다(환경 변수 미설정 = 현재 동작).

### 4.3 환경 변수

| 변수 | 클라우드 값 | 로컬 값 |
|------|-------------|---------|
| `D4D_DATA_DIR` | `/data` | 미설정(프로젝트 루트) |
| `D4D_HARDWARE_MODE` | `disabled` | 미설정(`local`) |
| `D4D_AUTH_TOKEN` | 호스팅 시크릿 | 미설정 |
| `D4D_LLM_PROVIDER`, `OPENAI_API_KEY` 또는 AWS 자격 | 호스팅 시크릿 | `.env` |
| `D4D_FC_PORT`, `D4D_HARDWARE_WRITES` | 설정하지 않음 | 필요 시 설정 |

### 4.4 안전 불변 조건

- 클라우드 빌드는 `D4D_HARDWARE_MODE=disabled`를 **이미지 기본값**으로 굽는다. 운영자가 실수로 `local`로 켜도 컨테이너에는 시리얼 장치가 없어 `fc_port_unavailable`로 실패한다.
- `D4D_HARDWARE_WRITES`는 클라우드 환경 변수 목록에 넣지 않는다.
- UI는 어떤 모드에서도 "비행 가능"을 표시하지 않는다(PRD 8절, `HARDWARE_CONTROL.md` 6절 유지).

### 4.5 데이터와 백업

- `/data` 볼륨 1GB로 시작(현재 artifacts 1.1MB, scenarios 수 KB).
- 주 1회 `artifacts/`·`scenarios/`를 zip으로 내려받는 수동 백업부터 시작. 자동화는 필요해질 때 S3/GCS 동기화로.
- 블랙박스 로그(`logs/`)는 올리지 않는다(로컬 분석용).

## 5. 단계별 실행 계획

| 단계 | 작업 | 검증 |
|------|------|------|
| 1 | 4.2의 1~3, 6 구현 + 단위 테스트(`tests/test_web.py`에 disabled 모드·data dir 케이스) | `python -m unittest` 통과, 로컬 기본 동작 불변 |
| 2 | Dockerfile 작성, 로컬 `docker build` → `docker run -e D4D_HARDWARE_MODE=disabled` | `/api/health`, 계획 스트림 1회, 라이브러리 목록 |
| 3 | 4.2의 4(UI disabled 표시) + 5(토큰 인증) | Vitest, 브라우저에서 토큰 없이 401·있으면 정상 |
| 4 | 호스팅 선택·시크릿 등록·볼륨 마운트·배포 | 공개 URL에서 시나리오 생성→계획→패키지 다운로드 E2E |
| 5 | `docs/DEPLOY.md`, README 배포 절 | 문서대로 처음부터 재배포 가능 |

예상 변경 규모: Python 약 120줄, TS 약 80줄, Dockerfile 30줄, 테스트 6개 내외.

## 6. 결정이 필요한 항목

1. 호스팅: Fly.io(볼륨 지원, 서울 리전 있음) vs Render(간단, 디스크 유료) vs Cloud Run(볼륨 대신 GCS 어댑터 필요 → 변경 커짐). **Fly.io 권장.**
2. 인증 수준: 공유 토큰 한 개(권장, 1차)로 충분한지, 아니면 사용자별 계정이 필요한지.
3. LLM 공급자: 클라우드에서도 `openai`를 쓸지, 키 비용 한도를 둘지.
4. 도메인: 호스팅 기본 도메인(`*.fly.dev`)으로 시작할지 자체 도메인을 붙일지.

이 네 가지가 정해지면 5절 1단계부터 착수한다.

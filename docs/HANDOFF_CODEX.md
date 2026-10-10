# 전술지휘소(SPIDER 웹 콘솔) · 접근 시뮬레이터 인수인계 — Codex용

작성 2026-10-10, Codex 인수 후 -25 지원으로 갱신. 시작 기준은 `87adbd6`이며 `d2ed469` 이후 변경은 펌웨어 저장소 주소 안내뿐이다. 모르는 게 있으면 아래 "참고 문서"부터 읽는다.

## 0. 한 줄 요약

- 저장소: `https://github.com/bckim0123-lab/d4d_2` (`main`). 웹 콘솔은 `web/`, API는 `src/d4d_web/`, 계획기·FC 도구는 `src/d4d_planner/`, `src/d4d_fc/`.
- 펌웨어(XIAO ESP32S3, `TAGVISION-20261010-25`)는 **별도 저장소** `https://github.com/bckim0123-lab/d4d_firmware`(비공개, `main`, 소스 `tag-board-fw/src`, 도구 `tools/`)다. 콘솔이 알아야 할 인터페이스(제어기 헤더·상수·실제 텔레메트리 샘플)는 이 저장소 `hardware/reference/firmware-25/`에도 복사해 두었다. 콘솔 작업에서 펌웨어를 고칠 일은 없다.
- 안전 규칙(3절)은 바꾸지 않는다. UI는 아밍·스로틀·모터·원시 MSP를 절대 보내지 않는다.

## 1. 실행·테스트

```powershell
pip install -e .            # 최초 1회
cd web; npm install; cd ..
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1     # API 8765 + Vite 5173
```

- Vite 프록시는 `web/vite.config.ts`에서 `SPIDER_API`(기본 `http://127.0.0.1:8765`)로 `/api`를 넘긴다. 포트를 바꾸면 둘 다 맞춘다.
- 백엔드만: `$env:PYTHONPATH='src'; python -m uvicorn d4d_web.api:app --port 8765`
- 테스트: `python -m unittest discover -s tests`, `cd web; npm test -- --run`, `npm run build` (tsc 포함, `noUnusedLocals` 켜져 있음). 최신 실행 결과는 `docs/CODEX_VERIFICATION.md`에 기록한다.
- 인수 시 웹 43개와 빌드 통과. Python은 수집 168개지만 `out/fc/fc_inspect.json` 없는 checkout에서 클래스 1개(테스트 2개)와 개별 테스트 1개가 건너뛰어져 `Ran 166, skipped=2`였다. 실제 기기 조회로 증빙을 채우지 않았다.
- LLM 없이(`D4D_LLM_PROVIDER=none`) 전체 데모가 동작한다. `.env`는 커밋하지 않는다.
- 실기기: Web Bluetooth는 Chrome/Edge + `localhost` 또는 https에서만 된다. 보드 BLE 이름 `TagBoard-B8B4`, Nordic UART Service.

## 2. 지금까지 된 것

### 전술지휘소
- 전장 편집기·에이전트 콘솔·계획 SSE·승인 게이트·플랜 카드·EMP 드릴·미션 라이브러리(PRD 5.1~5.4) 완료.
- 실기기 제어 패널(PRD 5.5, `web/src/HardwareControlBay.tsx`, `web/src/hardware.ts`): -25 텔레메트리, CH6 제어권과 50Hz 송신 구분, rcmap 기반 스로틀 슬롯, authority/vz 이벤트, 안전 게이트, RAM 마스크 7/64/67/71 2단계 시험, **접근 제어 카드**(shadow/off/reset/params, on/legacy 2단계 확인, 파라미터 set).
- 순수 함수 설계: 파싱·게이트·표시 판정은 전부 `hardware.ts`의 순수 함수(`parseTelemetryLine`, `deriveControlPanelState`, `deriveApproachView`, `serializeBoardCommand`, `serializeApproachSet` …)이고 테스트가 고정한다. UI 컴포넌트는 얇게 유지한다.

### 접근 시뮬레이터 (심사용)
- `web/src/approachSim.ts`: 펌웨어 `approach.h`(-25 기준)를 TS로 옮긴 `ApproachController`, 기체 모델 `ApproachSimulation`(스틱 1µs = 1cm/s², 자세 지연 0.3s, 항력, 잡음, 카메라 300ms, 55° 시야 반경 h·tan27.5°), 각본 6종과 테스트 23종. 중립 복귀·과속 제동·마스크 fault를 반영했다. 수치적분은 20Hz이며 보드 RC 송신 50Hz와 구분한다.
- 핸드오버는 `approach.h` 밖의 기준점 동작을 문서에 따라 설명하는 별도 시각 모델이다. 기본 100µs/s 대신 해당 각본만 10µs/s를 써서 변화를 보여준다. 실기기 송신 지연·비행 성능 검증이 아니다.
- `web/src/ApproachSimulator.tsx`: 전체 화면 가상 시험실. `SimulationScene.tsx`의 Three.js 장면, 속도/거리 차트, 계측·외란·설정 탭, 입력 기록 재생, 결과 카드를 제공한다. 상단 `시뮬레이터` 및 하단 베이에서 연다. 1440×900·1100×800에서 2열, 960px 이하 1열. CSS는 `simulator.css`와 `simulationScene.css`. 재생 회귀 테스트는 9종이다.
- 플래카드 `SIMULATION · 실기기 아님` 상시. 시뮬레이션은 보드·FC로 아무것도 보내지 않는다.
- 지도 확대·이동과 명시적 편집, 편집·계획·기록 탭, 하드웨어 패널 3개 탭을 추가했다. 후속 UI 검증과 자산 출처는 `docs/UI_UX_REFRESH.md`에 기록한다.

## 3. 절대 유지할 안전 규칙

1. UI는 아밍, 스로틀, 모터 시험(`motortest`), 원시 MSP(`fcmsp`), `voicecmd`, 자유 입력 명령을 보내지 않는다. 허용목록은 `hardware.ts`의 `BOARD_COMMANDS`뿐이다(테스트가 거부를 고정).
2. FC 쓰기는 `msp_override_channels` **RAM 전용** 변경 한 가지. EEPROM `save` 없음. `D4D_HARDWARE_WRITES=1` + 확인 문구 `프로펠러 제거 / 디스아밍` + 30초 일회성 토큰.
3. `approach on|legacy`는 2단계 확인 + `groundReady`(지상 시험 게이트 전부 열림)일 때만. `max_speed` UI 상한 10cm/s.
4. 어떤 상태에서도 "비행 가능"을 표시하지 않는다. 보고가 끊기면(2초) 안전으로 해석하지 않는다(fail-closed).
5. 펌웨어 보드가 스로틀 슬롯에 1100µs 이상을 보내면 지상 제어를 잠근다(`-24` 이전 펌웨어 대응).

## 4. 인수 당시 우선순위와 처리 상태

1. **완료 — 펌웨어 -25 텔레메트리 반영** (`hardware.ts` + 테스트 + 카드)
   - `msp` JSON 새 필드: `tx_hz, tx_always, rcmap, rcmap_src, fc_mask, mask_ok, mask_recommended, thr_slot, thr_slot_us, base_rpy, pilot_rpy, pilot_valid, auto_since_ms, vz_cm_s, vz_warn, vz_warn_count`.
   - `approach` JSON 새 필드: `neutral, overspeed, vz_cm_s, vz_warn, mask_ok, out_rpy, thr_slot_us, rc_tx`. 새 fault: `mask`, `mask_unknown`.
   - 새 타입: `authority`(CH6 전환 이벤트: `to, ch6, t, frame_age_ms, tx_hz, out_rpy, thr_slot_us, pilot_rpy, pilot_valid, fc_rc, approach`), `warn`(`code:"vz", vz_cm_s, limit_cm_s, height_cm, height_src, throttle_action:"none"`).
   - `approach_mode`의 `fc_mask`, `warning` 반영. 새 파라미터 UI 범위는 중립 플래그 0/1, `overspeed` 1~12cm/s, `handover_slew` 10~1000µs/s. `max_speed` 상한 10은 그대로다.
   - `rc_tx:true`는 송신만 뜻한다. FC 적용 제어권은 최신 `authority`로 구별한다. `thr_slot`/`rcmap` 불명·충돌, `rc_tx` 누락은 fail-closed. MSP/접근 보고는 2초, FC 조회는 8초이며 송신 직전 실제 시각으로 다시 확인한다.
2. **완료 — 마스크 7/71 허용**: API·웹·writer에서 명시적으로 7/64/67/71만 허용한다. 7/67/71은 전체 게이트, 64는 복구 게이트다. `can_apply_override`와 `can_apply_67` 호환 필드를 제공한다. 마스크 7에는 CH7 킬이 없으므로 보드 킬만으로 FC 정지를 보장하지 않는다.
3. **완료 — -25 제어기 정합**: 중립 기본값 1/1, 과속 기준 12, mask fault를 이식했다. `tag-lost`만 `lostNeutral:0`, `wind`만 `deadbandNeutral:0`인 비교 각본이다. 참조 호스트 파일은 과거 문서에 13종으로 기록됐지만 현재 `main()`은 12개 테스트 함수를 호출한다. 복사된 소스의 기대 동작을 기준으로 검증했다.
4. **브라우저 리허설**: 1440×900 결과와 보완한 UX는 `docs/CODEX_VERIFICATION.md` 참고. 초기 흰 화면, 원본 JSON 숫자 재직렬화에 따른 승인 해시 불일치, 대피소 호출명 누락, 패치 취소, 드릴 최종 상태 유지, 카드 한글 표시를 보완했다. 원본 시나리오는 ID만 보내며 승인 해시 검증은 약화하지 않았다.
5. **미착수 — 배포**: 설계만 있다(`docs/plans/2026-10-10-cloud-deployment-design.md`). 대상 계정·호스팅·접근 정책·비용 범위를 정한 뒤 별도 진행한다.
6. **완료 — 문서 동기화**: `docs/HARDWARE_CONTROL.md`, PRD, README 웹 섹션과 수동 스로틀 시험 문서를 -25 기준으로 갱신했다. 실제 하드웨어 연결·설정 변경·펌웨어 업로드는 수행하지 않았다.

## 5. 펌웨어 인터페이스 요약 (-25)

- 전송: BLE NUS 또는 USB 시리얼, **한 줄 = JSON 객체**(`\n` 구분, 최대 1400B). 명령도 한 줄 텍스트(`info`, `approach params`, …).
- 실제 보드에서 받은 샘플: `hardware/reference/firmware-25/telemetry-samples.jsonl` (info, msp, fc, flow, vision, approach, approach_params, mic). `authority`·`warn`은 FC가 켜져 있어야 나와서 샘플이 없다(필드는 4절 참고).
- 주기: `approach` 5Hz, `msp`·`fc` 1Hz, `flow` 5Hz, `vision` 프레임마다(2fps), `authority` 전환 시, `warn vz` 1초 1회.
- 제어기: `approach.h` 그대로. 상태 `IDLE/APPROACH/ARRIVING/ARRIVED/HOLD/FAULT`, fault 우선순위 `disabled → manual_authority → kill → mask_unknown → mask → height → flow → velocity`, 태그 유실은 `HOLD/tag_lost`. 모드 `off/shadow/on/legacy`, 부팅 기본 `shadow`.
- 채널: FC 논리 순서 롤0·피치1·요2·스로틀3(`fc_rc`, 마스크 비트). 프레임 슬롯은 `rcmap`(AETR이면 슬롯2=스로틀). 보드는 스로틀 슬롯에 1000 고정.
- 명령 허용목록(콘솔이 보내도 되는 것): `info`, `tags`, `snap`, `msp on|off`, `mission reset`, `kill`, `kill off`, `approach`, `approach params`, `approach shadow|off|reset`, `approach set <이름> <값>`, `approach on|legacy`(2단계). 나머지는 금지.
- 펌웨어 쪽 시험 절차·로그 증빙: `docs/plans/2026-10-10-manual-throttle-tracking-test.md`.

## 6. 참고 문서

| 문서 | 내용 |
|------|------|
| `docs/PRD.md` | 제품 요구사항. 5.5 제어 패널, 5.6 시뮬레이터, 10장 2분 데모 |
| `docs/HARDWARE_CONTROL.md` | −25 텔레메트리·제어권·마스크·안전 게이트와 지상 절차. 이전 실측 기록과 소프트웨어 지원 범위를 구분 |
| `docs/CODEX_VERIFICATION.md` | 인수 전후 테스트 집계, 1440×900 브라우저 리허설, 검증 범위와 미실시 항목 |
| `docs/plans/2026-10-10-hardware-control-design.md`, `-hardware-control.md` | 제어 패널 설계·구현 계획 |
| `docs/plans/2026-10-10-approach-control.md` | 접근 제어 분석·설계, 시야 제약, 지상 시험 6단계 |
| `docs/plans/2026-10-10-manual-throttle-tracking-test.md` | 펌웨어 −25 변경, FC CLI 절차, 검증 로그 필드 |
| `docs/plans/2026-10-10-cloud-deployment-design.md` | 배포 설계(미구현) |
| `docs/plans/2026-10-09-photoreal-command-ui*.md` | UI 디자인 방향(사진 질감 지휘소 룩) |
| `hardware/profile.json`, `기기정보/*.md` | 기체·배선·센서·FC 설정 기록 |
| `hardware/reference/firmware-25/` | `approach.h`, `config.h`, 호스트 테스트, 텔레메트리 샘플 |

## 7. 알려진 함정

- `tag_monitor.py --cmd`는 반드시 `--duration N`과 함께(아니면 프로세스가 안 끝남). 이 도구는 펌웨어 저장소에 있다. 콘솔 작업에는 필요 없다.
- `npm run build`가 `tsc -b`를 돌리므로 안 쓰는 import 하나로 실패한다.
- Vitest에서 `console.log`가 안 보이면 파일로 덤프한다.
- 시야 제약: HFOV 55°면 바닥 시야 반경이 높이 50cm에서 26cm다. 시뮬레이터 각본 출발점은 이 안에 있어야 하고(테스트가 고정), 지상 시험 안내문도 이 수치를 쓴다.
- 한국어 UI·문서. 상태를 색만으로 전달하지 않는다(텍스트 병기). `prefers-reduced-motion` 존중.

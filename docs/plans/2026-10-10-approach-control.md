# AprilTag 저속 접근 제어 — 1단계 분석과 구현 계획

작성 2026-10-10. 대상 펌웨어: `d4d_firmware/tag-board-fw` (`TAGVISION-20261010-22` 기준).
목표: 50cm 고도에서 태그가 가리키는 지점으로 최대 10cm/s, 25cm부터 감속, 5cm 안에서 정지.

## 1단계. 기존 코드·좌표계·통신 분석

### 1.1 지금 있는 파이프라인 (유지)

```
카메라(3~4fps) ─ apriltag ─ observe() ─ updateMission() ─┐ g_ctrl (bodyRight/bodyFwd cm, seen, atMs)
                                                          │
FC UART5 MSP 20Hz ─ mspTask ─ computeSticks() ←───────────┘ → MSP_SET_RAW_RC (롤·피치·킬)
         ↑ MSP_RC(CH6 제어권·ARM·스로틀) · SONAR_ALTITUDE(10Hz) · INAV_STATUS/ANALOG/MOTOR(2Hz)
```

| 구성 요소 | 위치 | 상태 |
|-----------|------|------|
| 태그 → 기체 좌표 | `observe()` main.cpp 414~452 | 호모그래피로 착륙점 투영 → `/g_fx` 정규화 → `imageToBody`(CAM_ROTATE_DEG) → `camRayToBody`(팬·틸트) → `groundFix`(높이 또는 태그 크기) → `bodyRight/bodyFwd`(cm). **카메라 보정(fx)·고도·설치각(짐벌)이 이미 반영됨** |
| 카메라 보정 | `g_fx = (W/2)/tan(HFOV/2)`, `CAM_HFOV_DEG 55` | 10/03 1회 실측(304px↔41cm). 렌즈 왜곡 모델 없음 |
| 높이 | `flowSnapshot()` → FC `MSP_SONAR_ALTITUDE` 10Hz | 오늘 1~118cm 추종 확인 |
| 제어권 | `RcAuthorityGate` CH6 900~1700=AUTO, 250ms 만료 | 오늘 수동 우선·자동 복귀 확인 |
| 출력 | `computeSticks()`: 롤 = 4µs/cm × bodyRight, ±80µs, 1초 stale → 1500 | **위치 비례 한 단계뿐.** 속도 루프·가감속 제한 없음. 4µs/cm는 미검증 값 |
| 광류 속도 | `flow` JSON `forward_cm_s:null` | **보드에 없음.** FC에만 있음(MSP2_INAV_OPTICAL_FLOW, 오늘 품질 34~62 수신 확인) |
| 안전 | CH6 수동 즉시 송신 중단, 킬 CH7, `msp off`, 스로틀·ARM 미덮어쓰기 | 유지. 접근 전용 조건(광류 품질·고도 범위·속도 이상·센서 나이)은 없음 |

### 1.2 구현 가능한 부분 (코드만으로)

1. 위치 오차: `bodyRight/bodyFwd`를 그대로 쓴다. 단위 cm, + = 목표가 기체 오른쪽/앞.
2. 거리 기반 감속 프로파일, 가속도 제한, 명령 변화율 제한: 순수 함수로 작성해 PC에서 시험.
3. 속도 추정: FC에서 `MSP2_INAV_OPTICAL_FLOW`(품질, 유량 X/Y deg/s, 자이로 X/Y deg/s)를 20Hz로 받아
   `v = deg2rad(유량 − 자이로) × 높이`로 계산. 보조로 태그 위치 미분(3~4Hz)도 계산해 교차 확인.
4. 속도 P 제어 → 롤·피치 µs. 20Hz `mspTask`에서 실행(기존 주기 그대로).
5. 안전 게이트 연결, 디버그 JSON, 테스트 모드(`shadow`: 계산만 하고 FC에는 중립 전송).

### 1.3 추가 검증이 필요한 부분 (값을 확정하지 않고 파라미터로)

| 항목 | 현재 근거 | 검증 방법 | 파라미터 |
|------|-----------|-----------|---------|
| **카메라 장착 회전** | 처음엔 270으로 추정했으나(조작자가 반대편에서 움직여 생긴 오판), 17:17 사용자 실물 확인: 기체 앞 = 화면 위 → `CAM_ROTATE_DEG=0`(현재 값 유지). 뷰어도 `--rot 0`으로 교정 | 태그를 기체 오른쪽으로 8cm 밀어 화면 왼쪽으로 가는지(좌우 거울 반전 여부) | `CAM_ROTATE_DEG` 0 |
| **광류 축·부호** | FC `align_opflow=CW0FLIP`. 오늘 느린 손 이동(3cm/s)으로는 반응 미확인 | 빠른 왕복 이동 중 `approach` 로그의 flow_v와 tag_v 부호 비교 | `FLOW_RIGHT_SIGN`, `FLOW_FWD_SIGN`, `FLOW_SWAP_XY` |
| **스틱 → 이동 관계** | ANGLE: 스틱=기울기(가속도). 10µs ≈ 0.6° ≈ 0.1m/s². POSHOLD: 스틱=속도이나 `nav_manual_speed` 500cm/s 기준이면 10cm/s=10µs로 데드밴드 안 | 프로펠러 제거·아밍 상태에서 롤 +40µs 명령 → 모터 차동 방향 확인(INAV Configurator 모터 탭) | `APPROACH_VEL_KP_US_PER_CMS`(초기 후보 2.0), `ROLL_SIGN`, `PITCH_SIGN` |
| **비행 모드** | 현재 CH8=ANGLE 상시. 고도 50cm 유지는 보드가 못 함(스로틀 미덮어쓰기, 설계상 유지) | INAV에 ALTHOLD(또는 SURFACE) 모드 스위치 할당 필요 → FC 설정 결정 사항 | 문서화만. 보드는 모드와 무관하게 동작하되 `approach` 로그에 FC 모드 플래그 표시 |
| **기체 기울기 보정** | 카메라가 기체에 고정 → 롤 5°에서 50cm 고도면 지면점이 4.4cm 이동(정지 반경 5cm와 같은 크기) | `MSP_ATTITUDE`(108) 수신 후 기체를 손으로 기울여 보정된 오차가 일정한지 | `ATT_COMP_ENABLED`(기본 off), `ATT_ROLL_SIGN`, `ATT_PITCH_SIGN` |
| **카메라-무게중심 오프셋** | 미측정 | 줄자 | `CAM_OFFSET_RIGHT_CM`, `CAM_OFFSET_FWD_CM` (0) |
| 속도 루프 게인 | 없음 | 지상(모터 차동) → 저속 비행에서 단계 조정 | `VEL_KP`, `VEL_KI`(0) |

### 1.4 설계 결정

- **구조**: 외부 루프 위치 P(→ 목표 속도, 10cm/s 상한, 감속 프로파일) → 가속도 제한 → 내부 루프 속도 P(→ 롤·피치 µs) → 변화율 제한 → ±상한. FC 자세 PID는 건드리지 않는다.
- **ANGLE 모드 전제**로 설계한다. POSHOLD에서는 FC가 이미 속도 루프를 가지므로 보드 속도 루프와 충돌한다. POSHOLD를 쓰려면 FC `nav_manual_speed_hor`를 50cm/s로 낮추고 보드는 위치 P만 쓰는 별도 모드가 필요하다(후속).
- **고도**: 보드는 스로틀을 보내지 않는다(기존 결정 유지). 50cm 유지는 INAV ALTHOLD/SURFACE 또는 조종자가 담당. 고도가 허용 범위(기본 20~150cm)를 벗어나면 접근 중지.
- **속도 추정 실패 시**: 목표 속도 0, 명령은 변화율 제한에 따라 1500으로 수렴. 위치 P만으로 계속 가지 않는다(요구 9).
- **기본 모드는 `shadow`**: 올려도 FC로 가는 값은 기존과 동일(중립). `approach on`으로만 실제 출력. 기존 `computeSticks`의 4µs/cm 위치 제어는 `approach legacy`로 남긴다.

## 2~5단계. 구현 (이 커밋)

새 파일 `src/approach.h` (하드웨어 의존 없음, PC 시험 가능):
- `ApproachParams` — 1.3의 모든 조정값. `approach set <이름> <값>`으로 재빌드 없이 변경(RAM).
- `ApproachController::step(input, dt) → output` — 상태 기계 IDLE/APPROACH/ARRIVING/ARRIVED/HOLD/FAULT.
- 속도 프로파일 `targetSpeed(dist)`: dist ≥ 25 → 10, 5 < dist < 25 → 10×dist/25 (거리 비례), ≤ 5 → 0. 처음에는 (dist−5)/(25−5) 램프를 썼으나 1D 시뮬에서 정지 반경 경계(5cm)에 점근하며 멈추지 않아 거리 비례로 바꿨다. 5cm 경계에서 2cm/s로 진입해 정지한다(요구 4 "거리에 비례").
- 추정 파라미터도 같은 표에 둔다: `flow_swap`, `flow_fwd_sign`, `flow_right_sign`(광류 축·부호), `att_comp`, `att_roll_sign`, `att_pitch_sign`(기울기 보정, 기본 끔), `cam_off_right`, `cam_off_fwd`(카메라→무게중심 cm).
- 가속도 제한(벡터 크기 기준 0.10m/s²), 명령 변화율 제한, 도착 판정(오차·속도·유지 시간).
- 고장 판정: 태그 stale, 광류 품질·나이, 고도 범위·나이, 속도 이상(기본 60cm/s), 제어권 수동, 킬.

`src/main.cpp` 변경:
- MSP 폴링 추가: `MSP2_INAV_OPTICAL_FLOW` 20Hz, `MSP_ATTITUDE` 10Hz.
- `computeSticks()`: 모드가 `on`이면 접근 제어 출력, `shadow`면 계산만, `legacy`면 기존 식.
- `approach` JSON 5Hz: 거리, 오차 X/Y, 목표 속도, 추정 속도(광류·태그 미분), 롤·피치 명령, 태그 상태, CH6, 상태·고장 사유, FC 자세, 광류 품질.
- 명령: `approach on|shadow|legacy|off`, `approach set <k> <v>`, `approach reset`, `approach params`.

`test/approach_host_test.cpp`: 프로파일 경계값, 가속도·변화율 제한, 도착 판정, 각 고장 전이, 1D 시뮬레이션(1차 응답 모델)에서 25cm→정지까지 오버슈트 없음.
빌드·실행: `uv run --with ziglang python -m ziglang c++ -std=c++17 -w -I src test/approach_host_test.cpp -o $env:TEMP\approach_test.exe; & $env:TEMP\approach_test.exe`
2026-10-10 결과: 9종 통과. 1D 시뮬(100cm 시작, 1µs=1cm/s², τ=0.3s): 목표 3.8cm 지점 정지, 최대 10.4cm/s, 오버슈트 0, ARRIVED.

### 6단계 1항 실측 (2026-10-10, 펌웨어 `TAGVISION-20261010-23`)

- shadow 모드로 올린 뒤 `approach` JSON 5Hz 수신 확인. FC 광류 q=17, 자세 롤 1.0°/피치 0.0°, 높이 2~3cm(바닥 위), CH6=2000(수동) → 상태 `FAULT` 사유 `manual_authority`, 롤·피치 명령 0 — 수동일 때 즉시 중지가 로그로 확인된다.
- `approach params`/`approach set vel_kp 2.0`/`approach set bogus 1`(→ cmd_error)/`info`(`"approach":"shadow"`) 동작 확인.
- 태그는 시야에 없어(`"tag":"none"`) 거리·프로파일 확인은 2항부터 태그를 놓고 진행한다.

> 2026-10-10 저녁 엔지니어 요청으로 펌웨어 `-25`에서 송신 방식(50Hz 상시)·핸드오버·마스크 확인·deadband/유실 중립이
> 바뀌었다. 그 시험 절차와 로그 증빙은 `2026-10-10-manual-throttle-tracking-test.md` 를 따른다. 아래 6단계는 그 이전 기준.

## 6단계. 지상 시험 절차 (프로펠러 제거)

시야 제약: HFOV 55°면 바닥 시야 반경은 \(h\tan 27.5^\circ\)라서 높이 50cm에서 약 26cm, 120cm에서 약 62cm다. 25cm 감속 반경 밖에서 접근을 시작하려면 50cm 높이에서는 태그가 거의 시야 가장자리여야 하므로, 프로파일 확인은 높이 100~120cm에서 하거나 태그를 25~26cm 안쪽에 두고 감속 구간만 본다. (관제 시뮬레이터 작성 중 발견, 2026-10-10)

1. `approach shadow`(기본)로 올린 뒤 태그를 50cm 아래에 두고 `approach` 로그에서 거리·목표 속도·명령이 프로파일대로 나오는지.
2. 태그를 밀어 tag_v 부호 확인 → `CAM_ROTATE_DEG` 확정.
3. 기체를 빠르게 왕복시켜 flow_v가 tag_v와 같은 부호인지 → `FLOW_*_SIGN` 확정.
4. 기체를 손으로 기울여 `ATT_COMP` 켠 상태에서 오차가 덜 흔들리는지 → `ATT_*_SIGN` 확정.
5. FC 아밍(조종기, 프로펠러 없음) + CH6 자동 + `approach on`: 태그를 오른쪽에 두면 INAV 모터 탭에서 오른쪽 모터가 줄고 왼쪽이 느는지 → `ROLL_SIGN`/`PITCH_SIGN` 확정. 15% 이하 스로틀.
6. 각 고장 주입(태그 가림, CH6 수동, 손으로 센서 가림)에서 1초 안에 명령이 1500으로 돌아오는지.

## 7단계. 저속 비행 검증 (사용자 수행)

실내, 그물 또는 테더, 조종자 CH6 즉시 수동 가능. ALTHOLD로 50cm 수동 유지 → CH6 자동 → 25cm 밖에서 시작 → 도착 판정 로그 확인. `VEL_KP`는 작은 값(1.0)에서 시작해 진동 없이 추종할 때까지 올린다.

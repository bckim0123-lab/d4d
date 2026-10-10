# SPIDER 실기기 제어 운용 절차

ESP32(XIAO ESP32S3 Sense)의 AprilTag 판단이 UART5 MSP를 거쳐 INAV FC의 롤·피치 수신 채널에
반영되는지를 **프로펠러 제거·디스아밍 상태**에서 확인하는 절차다. 이 문서의 어떤 단계도 이륙을
허가하지 않는다. 소프트웨어는 항상 비행 출력을 잠금으로 표시하며, 남은 실기기 검증은 6절에 적었다.

## 1. 정보 출처 우선순위

같은 항목이 서로 다르면 아래 순서로 믿는다.

1. **실기기 조회값**: `GET /api/hardware/fc/status`(COM9 MSP)와 XIAO BLE 텔레메트리(`info`, `msp`, `fc`)
2. **펌웨어 `-25` 참조**: `hardware/reference/firmware-25/`의 제어기·상수·호스트 테스트·텔레메트리 샘플,
   `docs/plans/2026-10-10-manual-throttle-tracking-test.md`의 인터페이스 설명
3. **이전 지상 기록**: `out/control_live/2026-10-10-ground-session.md`, GitHub `jikime/d4d` 2026-10-10 문서
4. **`C:\Users\neoar\Documents\d4d_2`의 CLI 덤프(2026-10-10 10:51)**: 그 뒤에 킬 설정(`aux 3 27 2 1700 2100`)과
   `motorstop_on_low=OFF` 변경이 있었으므로 모드·모터 정지 관련 항목은 더 이상 기준이 아니다.

`README.md` 상단의 2026-10-03 조사표(광류 없음, MSP RC override 비활성)는 **과거 기록**이다.
이 문서의 `-25` 설명은 소프트웨어 지원 계약이다. 이번 콘솔 변경으로 펌웨어 업로드나 FC 설정 변경,
실기기 재검증을 수행한 것은 아니다. 현재 연결된 기체의 빌드·마스크·링크는 매번 조회한다.

## 2. 구성 기록과 펌웨어 `-25` 지원

| 항목 | 값 |
|------|-----|
| FC | MicoAir H743 V2 (`MICOAIR743V2`), INAV 9.1.0, USB `COM9` |
| 보조 보드 | XIAO ESP32S3 Sense, BLE `TagBoard-B8B4`(Nordic UART). 15시 지상 기록은 `-22`, 이번 콘솔의 기준 인터페이스는 `TAGVISION-20261010-25` |
| 보드↔FC | UART5 MSP 115200. `-25`는 50Hz `MSP_SET_RAW_RC` 상시 송신 + `MSP_RC` 폴링. 실측 송신율은 `msp.tx_hz`로 확인 |
| MTF-01P | FC UART4 MSP 직결. 보드는 `height_src:"fc"`로 FC의 거리값을 읽음 |
| 수신기 | FlySky iBUS, UART6 |
| 채널 | CH5 ARM(1000 끔 / 2000 아밍), CH6 MSP RC OVERRIDE(1000 AUTO / 2000 MANUAL), CH7 FAILSAFE 킬, CH8 ANGLE 상시 |
| 영구 마스크 기록 | 15시 지상 기록은 `msp_override_channels = 64` (CH7만). 이후 엔지니어가 저장했을 수 있으므로 현재 영구값은 별도 확인 |
| 콘솔 RAM 허용값 | `7`, `64`, `67`, `71`만. 스로틀 비트 8·ARM 비트는 모두 제외 |

마스크 비트는 `rcmap`과 무관한 **FC 논리 채널** 기준이다. 롤=1, 피치=2, 요=4, 스로틀=8, CH7=64다.
프레임 배열 `out`의 슬롯은 `rcmap`에 따라 달라진다. AETR에서는 스로틀이 슬롯 2지만 다른 매핑에
`out[2]`를 그대로 쓰지 않는다. 패널은 `thr_slot`·`thr_slot_us`와 매핑의 일관성을 확인한다.

| 마스크 | 적용 채널 | 용도와 제한 |
|--------|-----------|-------------|
| `7` | 롤·피치·요 | 수동→자동 기준점 연결 시험. **CH7 미포함으로 보드 킬을 FC에 전달하지 못함** |
| `71` | 롤·피치·요·CH7 | 기준점 연결 + CH7 전달 시험. 킬 효과는 모드·링크·FC 설정 확인 필요 |
| `67` | 롤·피치·CH7 | 기존 지상 시험과 호환. 요는 조종기 소유 |
| `64` | CH7 | 수평 제어 권한 회수. 접근 제어에는 `mask` 고장 조건 |

### 보드 펌웨어 `-25`의 제어권 규칙

- CH6이 **900~1700**이면 AUTO, 그 밖이면 MANUAL. INAV의 MSP RC OVERRIDE 활성 범위와 같다.
- `MSP_RC` 응답이 **250ms** 넘게 없거나 부팅 직후, UART 오류면 MANUAL로 간주한다(fail-closed).
- MSP가 켜져 있으면 CH6 수동/자동과 무관하게 50Hz로 프레임을 보낸다(`tx_always:true`, `rc_tx:true`).
  `msp off` 또는 별도 모터 시험 중에는 멈춘다. UI는 모터 시험 명령을 제공하지 않는다.
- `rc_tx`는 **보드 송신 여부**다. FC 적용 여부는 최신 `authority:"auto"` 및 FC 모드·마스크로 판단한다.
  MANUAL에서는 FC가 보드 프레임을 적용하지 않고 조종기 입력을 사용한다.
- MANUAL 동안 보드는 `pilot_rpy`를 추적한다. AUTO 진입 때 그 값을 `base_rpy`로 고정하고
  `handover_slew`(기본 100µs/s)로 1500에 수렴시킨다. 접근 제어 오프셋은 이 기준점에 더한다.
- 보드 스로틀 슬롯은 1000µs 고정이고 FC 스로틀(`fc_rc[3]`/`rc_throttle`)은 조종기 소유다.
  프레임의 1000µs는 FC가 실제 적용하는 스로틀 값과 구분해 읽는다.
- `authority` 이벤트는 전환 시점의 증거다. 오래된 전환 이벤트만으로 현재 제어권을 확정하지 않는다.

## 3. 소프트웨어 구성

| 구성 요소 | 역할 | 쓰기 권한 |
|-----------|------|-----------|
| 브라우저(`web/src/HardwareControlBay.tsx`) | Web Bluetooth로 XIAO NUS 연결 소유, 텔레메트리 파싱, 허용목록 명령 전송, FC 상태 3초 폴링 | 보드 명령 허용목록만 |
| FastAPI(`src/d4d_web/hardware.py`) | COM9를 짧게 열어 FC 상태 조회, 일회성 토큰으로 RAM 마스크 변경 | `msp_override_channels` 7/64/67/71 RAM만 |
| 순수 판정기(`src/d4d_fc/control.py`, `web/src/hardware.ts`) | 게이트 통과/차단과 사유 계산 | 없음 |

### API

- `GET /api/hardware/profile`: 토폴로지 요약(`hardware/profile.json`, UID·MAC 제외)
- `GET /api/hardware/fc/status`: 식별, 무장, 차단 사유, 센서, RC, 모터, 모드, 직렬 포트, 마스크, 게이트
- `POST /api/hardware/fc/override/challenge` `{target: 7|64|67|71, confirmation}` → 30초 일회성 토큰
- `POST /api/hardware/fc/override/apply` `{target, token}` → 적용 직전 재조회, RAM 쓰기, 되읽기 검증

상태 응답의 `can_apply_override`는 7/67/71 공통 적용 게이트다. 기존 `can_apply_67`는 같은 값의 호환
필드로 남는다. `target_masks:[7,67,71]`, `allowed_masks:[7,64,67,71]`를 반환한다. 64 복구는
`can_restore_64`를 사용한다. 토큰은 확인한 대상에 묶이며 한 번 사용하거나 만료되면 다시 발급받는다.

### 환경 변수

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `D4D_FC_PORT` | `COM9` | FC USB 포트 |
| `D4D_FC_BAUD` | `115200` | MSP 속도 |
| `D4D_HARDWARE_WRITES` | 미설정 | `1`일 때만 RAM 마스크 변경 허용. 그 외에는 challenge가 403 |

확인 문구는 정확히 `프로펠러 제거 / 디스아밍` 이어야 한다.

### 보드 명령 허용목록

| 명령 | UI 버튼 | 비고 |
|------|---------|------|
| `info` `tags` `snap` | 상태 요청 | 조회 |
| `msp on` / `msp off` | MSP 제어 시작 / 중지 | 보드의 RC 송신 켜고 끔 |
| `mission reset` | 미션 초기화 | 정렬 단계로 |
| `kill` | 킬 / 비상 정지 | BLE 연결 중 즉시 보드에 요청. **FC 정지·디스아밍 보장이 아님**. 마스크 7에는 CH7이 없고, CH7 포함 마스크도 링크·모드·FC 설정과 실측 확인 필요 |
| `kill off` | 비상 정지 해제… → 해제 확정 | 2단계 확인, 6초 안에 확정해야 전송 |
| `approach` `approach params` | 접근 상태 / 파라미터 조회 | 조회만 수행 |
| `approach shadow` `approach off` `approach reset` | 접근 제어 카드 | 접근 계산·출력 오프셋 상태 변경. 프레임 송신과 기준점 복귀는 별개 |
| `approach on` / `approach legacy` | 출력 켬(on)… / 기존 위치 제어(legacy)… → 확정 | 2단계 확인 + `groundReady` 필요. 접근 롤·피치 오프셋 적용 모드로 전환 |
| `approach set <이름> <값>` | 파라미터 선택 + 값 + 적용 | 이름은 `APPROACH_PARAMS` 허용목록, 범위 밖·숫자 아님은 UI가 거부. `max_speed` 상한 10cm/s |

`motortest`, `fcmsp`, `voicecmd`, 임의 문자열, 아밍·스로틀 명령은 UI가 직렬화 자체를 거부한다
(`serializeBoardCommand`, `serializeApproachSet`). 원시 명령·모터 시험은 이 콘솔의 범위가 아니다.

### 접근 제어 카드와 텔레메트리 (펌웨어 `TAGVISION-20261010-25`)

보드의 AprilTag 저속 접근 제어기(복사본 `hardware/reference/firmware-25/approach.h`,
설계는 `docs/plans/2026-10-10-approach-control.md`)를
관제 패널에서 본다. 보드는 5Hz로 `approach` 줄을 보내며 카드가 표시하는 값은 다음과 같다.

| 표시 | 뜻 |
|------|-----|
| 상태 | 모드(`off`/`shadow`/`on`/`legacy`) · 상태(`IDLE` `APPROACH` `ARRIVING` `ARRIVED` `HOLD` `FAULT`) · 고장 사유 |
| 거리 · 오차 | 태그까지 수평 거리(cm), 기체 기준 오른쪽/앞 오차 |
| 목표 속도 · 벡터 | 감속 프로파일이 낸 속도 크기와 [오른쪽, 앞] 목표 속도 |
| 광류 속도 (q) | FC 광류×높이로 추정한 속도와 품질. 무효면 제어기는 FAULT |
| 태그 미분 속도 | 태그 위치 변화로 구한 속도. 광류 부호 검증용 |
| 롤 / 피치 명령 | 제어기 오프셋(µs). `shadow`/`off`에서는 접근 오프셋 미적용; RC 프레임 송신은 계속될 수 있음 |
| 중립 · 과속 | `neutral`, `overspeed`. 중앙/태그 유실 중립 정책 또는 과속 제동 여부 |
| 마스크 | `mask_ok`. 미확인 `mask_unknown` 또는 부적합 `mask`는 접근 출력을 차단 |
| 수직 속도 | `vz_cm_s`, `vz_warn`. 높이 변화 기록·경고이며 스로틀 제어 없음 |
| 기준점 · 송신 R/P/Y | `base_rpy`, `pilot_rpy`, `pilot_valid`, `out_rpy`로 조종기 기준점과 프레임 출력 구분 |

부팅 기본은 `shadow`(계산·보고, 접근 오프셋 미적용)다. 보고가 2초 넘게 끊기면 상태를 `unknown`으로
내리고 "안전"으로 표시하지 않는다. `on`은 지상 시험 게이트(`groundReady`)가 열려 있어야 켤 수 있다.
실제 접근 출력 적용은 AUTO 제어권, 7/67/71 마스크, 유효 센서·제어기 상태를 함께 확인한다.
고장 우선순위는 `disabled → manual_authority → kill → mask_unknown → mask → height → flow → velocity`다.
태그 유실은 `HOLD/tag_lost`이며 기본 `lost_neutral=1`에서 중립으로 수렴한다. `lost_neutral=0`이면
광류 속도로 제동한다. `overspeed` 초과는 목표 속도를 0으로 두고 제동하며, `max_vel` 초과는 `FAULT`다.

| 보고 종류 | `-25` 필드 |
|-----------|------------|
| `msp` | `tx_hz`, `tx_always`, `rcmap`, `rcmap_src`, `fc_mask`, `mask_ok`, `mask_recommended`, `thr_slot`, `thr_slot_us`, `base_rpy`, `pilot_rpy`, `pilot_valid`, `auto_since_ms`, `vz_cm_s`, `vz_warn`, `vz_warn_count` |
| `approach` | `neutral`, `overspeed`, `vz_cm_s`, `vz_warn`, `mask_ok`, `out_rpy`, `thr_slot_us`, `rc_tx` |
| `authority` | `to`, `ch6`, `t`, `frame_age_ms`, `tx_hz`, `out_rpy`, `thr_slot_us`, `pilot_rpy`, `pilot_valid`, `fc_rc`, `approach` |
| `warn` | `code:"vz"`, `vz_cm_s`, `limit_cm_s`, `height_cm`, `height_src`, `throttle_action:"none"` |
| `approach_mode` | `fc_mask`, `warning` |

`warn code:"vz"`는 기본 |vz|>10cm/s에서 최대 1초에 한 번 기록하는 경고다. 보드와 콘솔은 이를 이유로
스로틀을 조정하지 않는다(`throttle_action:"none"`). `null`은 정상값 0이 아니라 확인 불가로 읽는다.
복사된 실제 샘플은 FC 미연결 상태도 포함하며, `authority`/`warn` 전환 예시는 실기기 검증 기록이 아니다.

추가 파라미터의 **UI 허용 범위**는 다음과 같다. 펌웨어 setter의 범위 보장을 대신하는 콘솔 제한이다.

| 파라미터 | 기본값 | UI 범위 | 의미 |
|----------|--------|---------|------|
| `deadband_neutral` | 1 | 0 또는 1 | 중앙 영역 중립 수렴 |
| `lost_neutral` | 1 | 0 또는 1 | 태그 유실 시 중립 수렴(0은 광류 제동) |
| `overspeed` | 12cm/s | 1~12cm/s | 과속 제동 기준 |
| `handover_slew` | 100µs/s | 10~1000µs/s | 수동→자동 기준점의 1500 복귀 속도 |

`max_speed` UI 상한은 계속 10cm/s다. `overspeed`는 이상 속도에 대한 제동 기준이지 접근 목표 속도를
12cm/s까지 올리는 설정이 아니다.

파라미터는 전부 RAM 값이고 재부팅하면 펌웨어 기본값으로 돌아간다. 카메라 회전·광류 부호·스틱 부호·게인은
**실측 전 후보값**이므로, 변경한 값과 근거를 `out/control_live/`에 남긴다.

### 접근 시뮬레이션 (실기기 아님)

하단 베이와 접근 제어 카드의 `접근 시뮬레이션` 버튼은 같은 제어기 계산을 브라우저 안에서 돌리는 전체 화면
시연이다(`web/src/approachSim.ts`, `ApproachSimulator.tsx`). BLE나 FC로 어떤 명령도 보내지 않으며 화면에
`SIMULATION · 실기기 아님` 플래카드가 항상 떠 있다. 기체 모델(스틱 1µs = 1cm/s², 자세 지연 0.3s)은 가정이고,
실측 수치와 섞어 읽지 않는다. "롤 부호 반전" 조작으로 잘못된 부호의 영향을 관찰할 수 있지만,
시뮬레이터 결과가 실기기의 축·부호 검증을 대신하지 않는다.

각본은 6종이다. **수동 → AUTO 기준점 핸드오버**는 기준점 이동을 보기 쉽도록 `handover_slew=10µs/s`
(펌웨어 기본 100)로 재생한다. 핸드오버는 문서에 근거한 시각 모델이며 실제 50Hz 송신 시점이나 FC 지연을
검증하지 않는다. 태그 상실·복귀 각본은 `lost_neutral=0`의 광류 제동 비교이고, 바람 각본은
`deadband_neutral=0`과 `vel_ki=1`의 비교다. `-25` 기본 두 중립 정책은 1이다.

## 4. 안전 게이트

### RAM 마스크 7/67/71 적용 (`can_apply_override`, 기존 `can_apply_67` 호환)

| 게이트 키 | 조건 | 열리지 않을 때 할 일 |
|-----------|------|---------------------|
| `identity` | INAV 9.1.0 / `MICOAIR743V2` | 다른 기체에 쓰지 않는다 |
| `disarmed` | FC `armed == false`를 **직접 확인** | CH5를 1000으로 내리고 재조회 |
| `override_mask` | 현재 마스크가 7/64/67/71 중 하나 | 임의 값이면 변경 이력과 현재 설정부터 확인. 허용목록 밖 값은 API에서 쓰기 불가 |
| `throttle_low` | RC 스로틀 < `min_check`(1100) | 조종기 스로틀 최저 |
| `motors_idle` | M1~M4 = 1000µs | 모터 시험 종료 대기 |
| `receiver_link` | RC 링크 차단 없음, 채널 ≥ 4 | 수신기·조종기 전원 |
| `uart6_receiver` | UART6 `RX_SERIAL` | INAV Ports 확인 |
| `uart5_msp` | UART5 `MSP` | INAV Ports 확인 |
| `override_mode` | permanent ID 50 모드 범위 존재 | Modes 탭에서 CH6에 MSP RC OVERRIDE |

UI는 여기에 **XIAO BLE 온라인**, **보드 `fc_link:true`**, **CH6 정보 신선**, **MSP 보고 2초 이내**,
**FC 조회 8초 이내**를 더해 `groundReady`를 계산한다. 송신 중에는 스로틀 슬롯 매핑·값도 확인하며,
확인 불가 또는 1100µs 이상이면 적용을 잠근다. 하나라도 빠지면 7/67/71 적용 버튼이 잠긴다.

### RAM 마스크 64 복구 (`can_restore_64`)

백엔드는 `identity`, `disarmed`, `override_mask`만 요구한다. 수평 제어 권한을 회수하는 방향이므로
조건을 덜 둔다. UI는 여기에 BLE·FC·UART5 링크와 최신 MSP/CH6 상태를 요구한다. 복구도 같은 확인 문구와
30초 일회성 토큰 절차를 거치며 영구 저장하지 않는다.

### 즉시 잠금

BLE 끊김(stale), FC 조회 실패, `fc_link:false`, FC 아밍 감지 중 하나라도 생기면 일반 명령과 RAM 버튼이
비활성화된다. **킬 버튼은 BLE가 연결돼 있는 한 항상 활성**이다. 버튼 활성은 보드에 요청할 수 있다는
뜻이며 FC 정지를 보장하지 않는다. 특히 마스크 7은 보드 CH7 킬을 전달하지 못한다.

## 5. 첫 지상 제어 절차

사전: 프로펠러 전부 제거, 기체 고정, 배터리 연결, 조종기 전원, `D4D_HARDWARE_WRITES=1`로 API 실행.

1. 프로펠러가 없는지 다시 보고 기체를 묶는다.
2. 조종기 스로틀을 최저로 내린다(FC 조회에서 1100 미만).
3. CH5 ARM 스위치는 끈 상태(1000)로 둔다. CH6은 2000(MANUAL)로 시작한다.
4. FC USB(COM9)를 PC에, 브라우저에서 **XIAO 연결**을 눌러 `TagBoard-B8B4`를 페어링한다.
5. **제어 패널**을 열어 모터 4개 1000, 수신기 링크 통과, UART5 응답 OK를 확인한다.
6. 보드 `info`의 `build`, `rcmap`과 FC 마스크를 확인한다. `-25` 기준점 연결 시험은 7 또는 71을 선택한다.
   **7에는 보드 CH7 킬이 없다.** 확인 문구를 입력하고 대상 적용 요청 → 30초 안에 **RAM 적용 확정**.
   요청한 값의 되읽기와 보드 `mask_ok:true`, 스로틀 슬롯 1000을 확인한다. 67은 기존 롤·피치 시험용이다.
7. MANUAL에서 `rc_tx:true`, `tx_hz≈50`과 조종기 R/P/Y 추적을 확인한 뒤 CH6을 1000(AUTO)으로 내린다.
   최신 제어권이 `AUTO`인지 확인하고 전환 이벤트의 `frame_age_ms`, `out_rpy`, `pilot_rpy`를 기록한다.
   송신 표시는 전환 전후 모두 켜져 있을 수 있다.
8. 등록 태그(ID 0 = 100mm, ID 10 = 200mm)를 카메라 앞에서 좌우·앞뒤로 움직인다.
   보드 송신 롤/피치와 FC 적용 롤/피치가 함께 변하고 `일치`로 표시되는지 본다.
   펌웨어 `-25`는 부팅 시 `shadow`라 접근 오프셋이 적용되지 않는다. 기준점은 조종기 값에서 1500으로
   수렴할 수 있다. 기존 4µs/cm 반응을 보려면
   **기존 위치 제어(legacy)…** → 확정, 접근 제어기 출력을 보려면 **출력 켬(on)…** → 확정을 먼저 누른다.
   접근 제어 카드에서 거리·목표 속도·롤/피치 명령이 태그 위치에 따라 바뀌는지 본다. 태그를 가리면
   기본 설정에서 `HOLD`, `neutral:true`로 명령이 0에 수렴해야 한다. 이 동안 FC 스로틀과 보드 스로틀
   슬롯을 구분해 확인한다.
9. CH6을 2000(MANUAL)으로 올리고 최신 제어권과 조종기 입력 회수를 확인한다. `rc_tx:true`는 유지될 수
   있으며 접근 제어 카드는 `FAULT · manual_authority`로 바뀌어야 한다. **MSP 제어 중지**는 송신을 끄는
   별도 조작이다. 끝나면 **shadow (계산만)**으로 되돌린다.
10. 확인 문구를 다시 입력하고 **64 복구 요청** → **RAM 적용 확정**. 되읽기 64와 모터 1000 유지를 확인한다.

중간에 FC가 아밍되거나 모터가 1000을 벗어나면 시험을 중지하고 조종기의 CH5 디스아밍을 확인한다.
보드 **킬**도 요청할 수 있지만 마스크 7 등에서는 FC에 전달되지 않으므로 버튼만으로 정지를 판단하지
않는다. FC 무장 상태와 모터 값을 직접 다시 확인한다.

## 6. 비행 출력 잠금 조건

UI는 **항상** "비행 출력 잠금"을 유지하며 기체를 비행 가능으로 표시하지 않는다. 다음은 기존 지상 기록에
남은 검증 항목이며, 이번 소프트웨어 변경으로 완료 처리하지 않았다.

- 모터 순서·회전 방향: 10% 지상 구동 응답은 확인했으나 믹서 순서와 방향은 미확인
- 카메라/기체 축: `axes_verified:false`. 태그 이동 방향과 롤·피치 부호 실측 필요
- **태그에 따른 비중립 롤·피치 반영** 비교 미완료(이전 RAM 67 기록은 중립 1500/1500 일치만 확인)
- `-25`의 7/71 기준점 연결·마스크·스로틀 슬롯·수동 회수 실기기 검증 필요
- 공회전·킬 재시험: CH7 포함 마스크, 모드·링크 조건과 함께 FC 반응을 실기기에서 재확인 필요
- Failsafe 절차가 `DROP`인 점을 비행 전 재검토

## 7. 오류 처리

- COM9 사용 중(INAV Configurator 등): `fc_busy` 409. 쓰기 없이 "포트 사용 중"으로 표시
- FC 미연결: `fc_unavailable` 503. 상태 카드가 빨간색, RAM 버튼 잠김
- BLE 끊김: 마지막 값을 STALE로 표시, 명령 비활성. 사용자가 다시 **XIAO 연결**을 눌러야 재연결(자동 재연결 없음)
- 되읽기 불일치: `apply`가 409로 중단, EEPROM 쓰기 없음
- 토큰 만료(30초): challenge부터 다시. 브라우저 새로고침 시 토큰과 확인 문구는 사라짐
- RAM 변경 뒤 상태 조회 실패: 재부팅하면 마지막으로 EEPROM에 저장한 값으로 돌아간다. 과거 기록의
  64라고 단정하지 말고 엔지니어가 7/71을 저장했는지 확인한다. 콘솔은 EEPROM을 쓰지 않는다

## 8. 실기기 기록

- 2026-10-10 지상 세션 증거: `out/control_live/2026-10-10-ground-session.md`
- 펌웨어 `-25` 인터페이스 참조: `hardware/reference/firmware-25/`. 보드에서 복사한 보고와 테스트용
  합성 이벤트를 구분한다. 콘솔 테스트 통과는 실기기 재시험 완료를 뜻하지 않는다.

# 수동 스로틀 유지형 AprilTag 수평 추적 테스트 — 펌웨어 `TAGVISION-20261010-25`

엔지니어 요청(2026-10-10)을 XIAO 펌웨어에 반영한 내용과, FC 쪽에서 해야 할 설정, 로그로 증명하는 방법.
펌웨어 저장소: `d4d_firmware/tag-board-fw` (커밋 메시지 `-25`). 호스트 테스트 13종 통과, 빌드 통과. 보드 업로드는 COM 포트가 다시 잡히면 한다.

## 1. 바뀐 것 (요청 항목별)

### 1) CH6 기반 즉시 전환

| 요청 | 구현 |
|------|------|
| CH6 상태와 무관하게 `MSP_SET_RAW_RC` 50Hz 이상 상시 전송 | `MSP_SEND_HZ 50`, `MSP_TX_ALWAYS true`. mspTask 가 매 20ms 프레임을 보낸다. `msp off`·모터 시험 중에만 멈춤. 실제 송신율은 `msp` JSON `tx_hz` 로 1초마다 보고 |
| 수동 위치에서는 INAV 가 프레임 무시 | INAV 9.1 `rx/rx.c`: `IS_RC_MODE_ACTIVE(BOXMSPRCOVERRIDE)` 일 때만 `mspOverrideChannels()` 적용. 보드는 바꾼 것 없음(소스로 확인) |
| AUTO 진입 순간 최신 프레임 준비, 초기화 지연 없음 | 상시 송신이므로 FC 안에 항상 ≤20ms 프레임이 있다. 전환 시 `authority` 이벤트에 `frame_age_ms`(마지막 프레임 뒤 경과) 기록 |
| AUTO 직전 롤·피치·요가 수동 조종값과 연속 | **핸드오버 기준점**: 수동일 때 보드는 `MSP_RC` 로 읽은 조종기 롤·피치·요를 그대로 프레임에 실어 보낸다(FC 는 무시하지만 "준비된 프레임" = 조종기 값). AUTO 로 바뀌는 순간 그 값을 기준점으로 고정하고 `handover_slew`(기본 100µs/s)로 1500 에 수렴. 제어기 오프셋은 기준점 위에 더한다. AUTO 중에는 `MSP_RC` 가 우리가 덮어쓴 값을 돌려주므로 추적하지 않는다 |
| 수동으로 내리면 즉시 조종기 제어 | INAV 가 모드 비활성 즉시 수신기 값으로 돌아간다. 보드는 다시 조종기 값 추적 모드로. 이벤트 `authority to:"manual"` |

### 2) 스로틀은 항상 조종기 소유

| 요청 | 구현 |
|------|------|
| CH3 절대 override 안 함 | 프레임 스로틀 슬롯은 `MSP_THROTTLE_SAFE_US 1000` 고정(`min_check` 아래). 보드 코드 어디에도 스로틀을 계산해 넣는 경로가 없다(`CTRL_THROTTLE_ENABLED false`, 시뮬레이터 전용) |
| 실제 `rcmap`·`msp_override_channels` 를 먼저 읽는다 | 부팅 뒤 2초마다 `MSP_RX_MAP(64)` 과 `MSP2_COMMON_SETTING("msp_override_channels")` 조회. `msp`·`info` JSON 에 `rcmap:"AETR"`, `rcmap_src:"fc|default"`, `fc_mask`, `mask_ok` |
| 기본 AETR → 마스크 7, 스로틀 비트 8 제외 | INAV 소스로 확인: 마스크 비트는 **논리 채널**(롤 0·피치 1·요 2·스로틀 3) 기준이라 rcmap 과 무관하게 7 = 롤+피치+요, 8 = 스로틀. 보드 킬(ch7)까지 쓰면 71 |
| 매핑이 다르면 추측하지 않고 계산 | 프레임 슬롯은 `rcmap[논리]` 로 결정(INAV `msp_override.c` 가 `frame[rcmap[ch]]` 를 읽음). AETR 이면 `{0,1,3,2}` → 슬롯 2 가 스로틀. FC 에서 읽은 rcmap 이 0..3 순열이 아니면 기본값 유지 + `rcmap_src:"default"` |
| 스로틀 슬롯 값은 FC 가 무시하도록 | 마스크에 스로틀 비트가 있으면 접근 제어기가 `fault:"mask"` 로 출력을 막고, `approach on` 응답에 경고를 넣는다. 마스크를 아직 못 읽었으면 `fault:"mask_unknown"` |
| PID·UART·MSP·Failsafe 변경 없음 | 보드는 FC 설정을 쓰지 않는다. (관제 시스템의 RAM 마스크 쓰기도 이번 시험에서는 쓰지 않는다 — 아래 5절) |

### 3) MTF-01 처리

- 높이는 FC 거리센서(`MSP_SONAR_ALTITUDE` 10Hz) 또는 보드 직결 MTF 값. 보드가 `vz`(Δh/Δt, EMA 0.3)를 계산해 `approach` JSON(`vz_cm_s`, `vz_warn`)과 `msp` JSON(`vz_cm_s`, `vz_warn`, `vz_warn_count`)에 기록.
- |vz| > 10cm/s 면 1초에 한 번 `{"type":"warn","code":"vz",...,"throttle_action":"none"}`. 스로틀 슬롯은 변하지 않는다(로그의 `thr_slot_us` 가 항상 1000).
- 높이가 600ms 이상 끊기면 vz 무효(`null`). 수동 스로틀과는 데이터 경로가 분리돼 있어 영향이 없다.

### 4) AprilTag 수평 추적

- AUTO 에서만 출력(`fault:"manual_authority"` 가 수동에서 막는다). 태그 중심 오차 → 거리 비례 속도 프로파일(≤10cm/s) → 가속도 제한 → 광류 속도 P(I) → 롤·피치 오프셋(±80µs, 100µs/s 슬루).
- 실측 수평 속도 상한: `overspeed`(기본 12cm/s) 초과 시 그 스텝 목표 속도 0 → 제동. `max_vel`(60) 초과는 FAULT.
- 중앙 deadband(`stop_radius` 5cm) 안에 들어오면 `deadband_neutral=1`(기본): 롤·피치 중립으로 수렴. 2초 유지(`arrive_hold_ms`)면 `ARRIVED` — 수평 이동 없음, 고도는 조종자.
- 태그 유실·신뢰도 저하: `lost_neutral=1`(기본) → 즉시 중립 수렴(HOLD, `tag_lost`). 광류 품질 저하는 `fault:"flow"` → 중립. 예전처럼 광류로 제동하려면 `approach set lost_neutral 0`.
- CH6 수동: FC가 조종기 입력으로 돌아가고 제어기는 `manual_authority` fault로 명령을 0에 수렴시킨다. 보드 킬은 `kill` fault이며 FC CH7 전달에는 마스크의 비트 64가 필요하다. 마스크 7에는 CH7이 없으므로 보드 킬만으로 FC 디스아밍을 보장하지 않는다.

### 5) 검증 로그

보드 JSON 으로 아래를 증명한다(`uv run --with pyserial python tools\tag_monitor.py --port COMx --raw --duration 60` 로 원문 저장 권장).

| 증명할 것 | 보는 필드 |
|-----------|-----------|
| 상시 송신 중에도 수동이면 조종기만 제어 | `msp.rc_tx:true`, `msp.tx_hz≈50`, `authority:"manual"` 인 동안 `fc_rc[0..2]` 가 조종기 스틱을 따라 움직이고 `out` 과 같다(추적 중). 스틱을 움직여도 기체 반응이 조종기 그대로 |
| AUTO 전환 시 지연 없이 수평 제어 시작 | `{"type":"authority","to":"auto","frame_age_ms":≤20,"out_rpy":[...]≈"pilot_rpy":[...]}` 그리고 다음 `approach` 보고(≤200ms)에서 `state` 가 `APPROACH/ARRIVING` |
| AUTO 중에도 CH3 는 조종기와 일치 | `msp.rc_throttle`(= `fc_rc[3]`)가 스로틀 스틱을 따라 변하고, `thr_slot_us:1000` 고정, `fc_mask` 에 8 없음(`mask_ok:true`) |
| 수평 속도 ≤ 0.10 m/s | `approach.v_flow` 크기와 `target_speed` 가 10 이하, `overspeed:false` (넘으면 `true` 로 제동 기록) |
| MTF vz 는 기록·경고만 | `warn code:"vz"` 가 나와도 `thr_slot_us` 와 `out` 스로틀 슬롯이 1000 그대로, `rc_throttle` 은 조종기 값 |
| CH6 수동·킬에서 즉시 복귀 | `authority to:"manual"` 이벤트 뒤 `approach.fault:"manual_authority"`, `roll_us/pitch_us → 0`. 킬은 `fault:"kill"` |

## 2. FC 쪽 절차 (INAV CLI, 변경 전 백업 필수)

```
# 백업 — 출력 전체를 파일로 저장
diff all
rcmap
get msp_override_channels

# AETR(기본)이고 보드 킬(ch7)을 쓰지 않으면
set msp_override_channels = 7
# 보드 kill 명령까지 FC 에 반영하려면 (ch7 비트 64 포함)
set msp_override_channels = 71
save
```

- `rcmap` 출력이 `AETR` 이 아니면 그래도 마스크는 7/71 이다(논리 비트). 보드는 프레임 슬롯만 rcmap 에 맞춰 바꾼다. 보드 `info` 의 `rcmap` 값이 CLI 와 같은지 확인한다.
- 이전 설정 64/67 은 요 비트가 없고(67 은 롤·피치·킬), 이번 시험의 "롤·피치·요 연속 핸드오버"를 쓰려면 7 또는 71 로 바꾼다. 67 도 `mask_ok:true`(스로틀 없음, 롤·피치 있음)로 통과는 한다.
- PID·포트·Failsafe 는 건드리지 않는다.

## 3. 지상 확인 순서 (프로펠러 제거)

1. 보드 `-25` 업로드 → `info` 에서 `build`, `tx_always:true`, `rcmap`, `fc_mask`, `mask_ok:true` 확인.
2. CH6 수동(2000)에서 스틱을 움직이며 `msp.out` 롤·피치·요가 `fc_rc` 를 따라오는지(추적) + 스로틀 슬롯 1000 확인.
3. CH6 AUTO(1000) → `authority` 이벤트의 `frame_age_ms`, `out_rpy≈pilot_rpy` 확인. 스틱을 움직여도 FC 롤·피치·요는 보드 값(기준점→1500 수렴)으로, **스로틀만 조종기** 를 따라야 한다.
4. `approach on` → 태그를 시야에 두고 `approach` 보고로 거리·속도·명령 확인. 태그를 가리면 `HOLD tag_lost`, `neutral:true`.
5. 손으로 기체를 위아래로 10cm/s 넘게 움직여 `warn vz` 가 나오는지, 그동안 `thr_slot_us` 가 1000 인지.
6. CH6 수동 → 즉시 `authority to:"manual"`, 조종기 스틱이 FC 에 그대로.

## 4. 조정 가능한 값 (`approach set <이름> <값>`, 재부팅하면 기본값)

`deadband_neutral`(1) · `lost_neutral`(1) · `overspeed`(12) · `handover_slew`(100) 와 기존 `max_speed`(10, 상한) · `slow_radius` · `stop_radius` · `max_accel` · `vel_kp` · `vel_ki` · `cmd_max` · `cmd_slew` · `roll_sign` · `pitch_sign` · 광류·자세 부호들. 전부 실측 전 후보값.

## 5. 관제 시스템(d4d_2) 반영 상태

콘솔에 `-25` 인터페이스와 지상 시험 마스크 지원을 반영했다. 아래 내용은 소프트웨어 동작이며,
펌웨어 업로드·FC 영구 설정 변경·실기기 시험을 새로 수행했다는 뜻은 아니다. 이 문서의 원래 엔지니어
시험은 2절의 FC CLI 절차를 전제로 하며, 콘솔 RAM 시험 절차는 `docs/HARDWARE_CONTROL.md`를 따른다.

- `override_mask` 게이트와 RAM 쓰기는 **7/64/67/71만** 허용한다. 7/67/71 모두 기존 디스아밍·저스로틀·
  모터 정지·수신기·포트·모드 게이트를 통과해야 한다. 64 복구만 기존 축소 게이트를 유지한다.
  `can_apply_override`가 공통 적용 결과이고 `can_apply_67`는 호환 필드다.
- RAM 변경은 `D4D_HARDWARE_WRITES=1`, 정확한 확인 문구 `프로펠러 제거 / 디스아밍`, 대상에 묶인
  30초 일회성 토큰, 적용 직전 재조회와 되읽기 검증을 모두 유지한다. 콘솔은 `save`/EEPROM을 쓰지 않는다.
- 7은 롤·피치·요만 포함하므로 **보드 CH7 킬을 FC에 전달하지 못한다**. 71에는 CH7이 포함된다.
  킬 버튼이 켜져 있어도 FC 정지를 보장하지 않으며 링크·모드·FC 설정과 실제 무장/모터 상태를 확인한다.
- `msp.rc_tx`는 수동에서도 `true`일 수 있다. 패널은 50Hz 송신(`tx_hz`, `tx_always`)과 최신
  `authority` 제어권을 구분하고 `authority` 전환 이벤트를 표시한다. 오래된 보고는 안전으로 해석하지 않는다.
- 스로틀 논리 비트 8과 프레임 슬롯은 구분한다. 패널은 `rcmap`, `rcmap_src`, `thr_slot`, `thr_slot_us`를
  사용하며 `out[2]` 고정 가정을 없앴다. 송신 슬롯을 확인할 수 없거나 1100µs 이상이면 지상 적용을 잠근다.
- `msp`의 마스크·조종기 R/P/Y·기준점·수직 속도 보고와 `approach`의 `neutral`, `overspeed`, `mask_ok`,
  `out_rpy`, `thr_slot_us`, `rc_tx`를 읽는다. `mask`/`mask_unknown` 고장과 `approach_mode.warning`도
  표시한다. `warn code:"vz"`는 `throttle_action:"none"`으로 기록하며 스로틀 조치는 없다.
- 새 `approach set` UI 범위는 `deadband_neutral`/`lost_neutral` 0 또는 1, `overspeed` 1~12cm/s,
  `handover_slew` 10~1000µs/s다. `max_speed` 상한은 계속 10cm/s이며 모든 파라미터는 RAM 값이다.
- 시뮬레이터 제어기는 복사된 `approach.h`와 호스트 테스트를 기준으로 중앙·태그 유실 중립 정책,
  과속 제동과 마스크 고장을 표현한다. 수동→자동 기준점 연결은 문서 기반 시각 모델이며,
  실제 50Hz 송신 시점이나 FC 지연 검증을 대신하지 않는다.

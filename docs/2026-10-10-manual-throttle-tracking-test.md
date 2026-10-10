# 수동 스로틀 유지형 AprilTag 수평 추적 테스트 — 펌웨어 `TAGVISION-20261010-26`

엔지니어 요청(2026-10-10)을 XIAO 펌웨어에 반영한 내용과, FC 쪽에서 해야 할 설정, 로그로 증명하는 방법.
펌웨어 저장소: `https://github.com/bckim0123-lab/d4d_firmware` (`tag-board-fw`, 커밋 `-25` de4afc5 → `-26` 6f7feaf). 호스트 테스트 13종 통과.
**지상 검증 결과는 6절.** -26 은 -25 에서 FC 의 MSP 처리 한도 문제를 고친 것이다(아래 상자).

> **-26 에서 고친 것 — FC 는 MSP 포트당 초당 100 명령만 처리한다.**
> INAV 9.1 `msp/msp_serial.c` `mspSerialProcessOnePort()` 는 SERIAL 태스크 한 번(10ms, `fc_tasks.c` 100Hz)에 명령 **하나** 만 처리하고
> `break` 한다("process one command at a time so as not to block"). -25 는 50Hz 프레임 + 50Hz `MSP_RC` + 광류 25Hz + 높이 10Hz + 자세 10Hz 등
> 초당 약 152 개를 보내서 FC 가 절반을 버렸다(실측 ack 55%, `fcmsp` 질의 타임아웃). 즉 "50Hz 송신"이 FC 안에서는 ~29Hz 였다.
> -26 은 질의 예산을 총 94/s 로 줄였다: SET_RAW_RC 50 + `MSP_RC` 16.7(매 3주기, CH6 인지 ≤60ms) + 광류 10 + 높이 10 + 자세 2 + 상태 2 + 배터리 1 + 모터 1 + 설정 1.
> 증거 필드 `msp.ack_hz`(FC 가 1초에 ack 한 SET_RAW_RC 수)와 `rc_hz` 를 추가했다. **정상이면 `ack_hz ≈ tx_hz ≈ 50`.** `tag_monitor` 는 "FC수신 NNHz", 90% 미만이면 "⚠유실" 로 표시한다.
> 앞으로 FC 질의를 추가할 때는 `config.h` 의 예산 주석을 먼저 갱신한다.

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
- CH6 수동·킬: FC 가 즉시 조종기로 돌아가고, 제어기도 `manual_authority`/`kill` fault 로 명령 0.

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

## 5. 관제 시스템(d4d_2) 에 미치는 영향 — 아직 반영 안 함

- 지상 시험 게이트 `override_mask` 와 RAM 마스크 쓰기는 64/67 만 허용한다. 이번 시험대로 FC 를 7/71 로 바꾸면 게이트가 닫히고 `approach on` 버튼이 비활성화된다. 보드 명령은 `tag_monitor` 로 보내면 된다. 7/71 허용은 별도 작업. **FC 는 현재 7 이다(6절).**
- `msp.rc_tx` 가 수동에서도 `true` 가 되므로 패널의 "RC 송신 중" 표시 의미가 바뀐다(송신 ≠ FC 적용). `authority` 가 적용 여부다.
- `msp.ack_hz` 를 패널에 보여 주면 "FC 가 실제로 받고 있는가"를 심사위원 앞에서 바로 증명할 수 있다. `fc_link:true` 인데 `ack_hz < 0.9·tx_hz` 면 경고.

## 6. 지상 검증 결과 (2026-10-10 19:05~19:10, 프로펠러 제거, 배터리 3S 12.15V)

환경: 보드 `-26`, FC INAV 9.1 (엔지니어가 CLI 로 `msp_override_channels = 7` 설정, 보드가 `fcmsp 0x1003` 으로 직접 읽은 원문 `07 00 00 00`), `rcmap` 원문 `00 01 03 02` = AETR → 스로틀 슬롯 2.
기록: `tag_monitor --raw` 180초, 조종기로 수동 스틱 → CH6 AUTO → 스로틀 → CH6 수동. 분석은 로그 원문에서 뽑은 값이다.

| # | 증명할 것 | 결과 | 근거(로그) |
|---|-----------|------|-----------|
| 1 | 상시 송신 중에도 수동이면 조종기만 제어 | **확인** | `tx_hz 49~50`, `ack_hz 48~52`(FC 전량 수신), `authority:"manual"` 123 샘플 동안 FC 롤 1010~1997 · 피치 1041~1988 · 요 1355~1619 · 스로틀 1000~1971 로 스틱 그대로. 보드 `out` 롤·피치·요 = `fc_rc`(차이 0us, 추적 중). 스로틀 슬롯 1000 고정 |
| 2 | AUTO 전환 시 지연 없이 시작 | **확인** | `authority to:"auto"` 2건 모두 `frame_age_ms 20~21`(한 주기), `out_rpy` = `pilot_rpy` = 1500(점프 0us), `pilot_valid:true`. 전환 직후 FC 롤·피치·요가 보드 값으로 바뀜 |
| 3 | AUTO 중에도 CH3 는 조종기 | **확인** | 46초 AUTO 동안 FC 롤·피치·요 = 1500 고정(보드 값, 마스크 7 적용) 인데 FC 스로틀은 1000~1308 로 스틱 따라 변함. 보드 `thr_slot_us` 내내 1000. `mask_ok:true` |
| 4 | 수평 속도 ≤ 0.10 m/s | **미검증** | 바닥에 놓인 상태라 MTF-01 거리 무효(`height_valid:false`) → 제어기 `FAULT/height` 로 출력 0. 50cm 들어 올린 상태의 시험은 이번에 생략(사용자 결정). 상한은 호스트 테스트 `testSim1D`(최대 10.43cm/s, 과속 제동) 로만 확인된 상태 |
| 5 | MTF vz 는 기록·경고만 | **미검증(코드상 보장)** | 같은 이유로 `vz` 샘플 없음. 보드에 CH3 계산 경로가 없고 슬롯 상수 1000 이라는 점은 1·3 의 로그로 간접 확인 |
| 6 | CH6 수동 복귀 즉시 | **확인** | `authority to:"manual"` 3건, 복귀 직후 FC 채널이 스틱 값으로, 제어기 `fault:"manual_authority"`(출력 0). 킬 스위치(CH7)는 조작하지 않아 미검증 |

메모
- CH6 는 3단 스위치라 전환 중 1500 을 지나며, 1500 도 AUTO 범위(900~1700)라 `ch6:1500` 이벤트가 먼저 찍힌다. 동작엔 문제 없음.
- 기록 중 CH5 로 FC 가 아밍됐고 모터가 1906 까지 돌았다(프로펠러 없음). 수동 스로틀이 그대로 모터에 간다는 뜻이므로 3번의 추가 근거이기도 하다.
- `tag_monitor` 가 USB 포트를 열 때 보드가 리셋된다. 캡처 시작 직후 이벤트(`frame_age_ms 112`) 1건은 이 아티팩트라 제외했다. 실기 운용에는 영향 없음.
- 남은 4·5 번은 3절 4~5 순서(50cm, `approach on`, 위아래 흔들기)로 60초면 끝난다.

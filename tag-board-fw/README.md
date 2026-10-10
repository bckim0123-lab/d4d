# 태그인식 보드 펌웨어 (XIAO ESP32S3 Sense)

빌드: `TAGVISION-20261003-18`

- 카메라(OV2640, 640×480 회색조)로 AprilTag `tag36h11`(ID 0~586)를 인식한다.
- 인식한 태그의 의미를 해석하고 이동 방향을 판단한다.
- MicoAir MTF-01P(115200bps, 100Hz)에서 옵티컬플로우와 거리를 받는다. 센서 출력이 Micolink(기본)이든 MSP(INAV용 설정)든 자동으로 구분한다. MSP 플로우는 INAV와 같은 방식(`opflow_scale` 10.5)으로 cm/s로 바꾼다.
- 판단 결과를 **MSP로 FC(INAV)에 조종 채널 값으로 보낸다**(UART, 20Hz).
- 상태는 한 줄에 JSON 하나씩 **USB 시리얼과 블루투스(BLE)** 양쪽으로 내보내고, 명령도 양쪽에서 받는다.

## 빌드와 업로드

```bash
cd tag-board-fw
pio run -t upload --upload-port /dev/cu.usbmodem2101
```

## 블루투스(BLE) 연결

ESP32-S3는 BLE만 지원하므로(블루투스 시리얼 SPP 없음), **Nordic UART Service**로 USB 시리얼을 대신한다.

| 항목 | 값 |
|---|---|
| 광고 이름 | `TagBoard-XXXX` (MAC 끝 2바이트, 이 보드는 `TagBoard-B8B4`) |
| 서비스 | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (PC → 보드, 쓰기) | `6E400002-…` 명령 한 줄 + `\n` |
| TX (보드 → PC, 알림) | `6E400003-…` USB와 같은 JSON 줄 |

- USB와 동시에 쓸 수 있다. 블루투스는 연결해도 보드가 재부팅되지 않는다.
- 속도는 약 7KB/s다. 상태 줄(2~4KB/s)은 여유가 있고, 카메라 스냅샷(약 30KB)은 4초쯤 걸린다. 스냅샷은 1000자씩 나눈 여러 줄(`SNAP_BEGIN` … `SNAP_END`)로 보낸다.
- 못 보낸 줄은 `info`의 `ble_dropped`에 센다. `ble_buf`는 BLE 전송 버퍼(여유/전체), `ram_min`은 내부 RAM 최저 여유다.
- 20261003 시험: Mac에서 연결해 스냅샷 5번을 연속으로 받았고 모두 온전했다(누락 0).

**안정화하며 찾은 문제 (되돌리지 말 것)**

| 증상 | 원인 | 조치 |
|---|---|---|
| 연결 직후 또는 몇 초 뒤 데이터가 멈추고 끊김. 로그 `assert llc_con_upd.c 488` | 연결 직후 보드가 연결 간격·DLE·PHY 변경을 요청하면 macOS 갱신과 겹쳐 BLE 컨트롤러가 망가짐 | 연결 직후 파라미터 요청을 하지 않음 |
| 카메라가 켜지면 연결 실패. 로그 `BLE_INIT: Malloc failed` | AprilTag의 작은 할당이 내부 RAM을 다 써서(최저 324B) BLE 컨트롤러가 메모리를 못 받음 | `heap_caps_malloc_extmem_enable(32)`로 검출 중 할당을 PSRAM으로 → 최저 약 91KB |
| 알림 일부 누락, 연속 전송 시 정지 | 전송 버퍼를 몰아 써서 스택 몫이 남지 않음 | BLE 버퍼 24개, 8개는 스택 몫으로 남김, 알림 간격 4ms |

## 마이크와 음성 명령 (INMP441)

| INMP441 | XIAO | 비고 |
|---|---|---|
| VDD | 3V3 | 3.3V 전용 |
| GND | GND | |
| SCK (BCLK) | D3 (GPIO4) | I2S 비트 클록 |
| WS (LRCLK) | D4 (GPIO5) | 좌우 채널 클록 |
| SD | D5 (GPIO6) | 음성 데이터 |
| L/R | GND | 왼쪽 채널. 펌웨어가 소리가 실린 채널을 자동으로 고른다 |

- 16kHz로 받아 1초마다 `mic` 메시지로 소리 크기(dBFS)를 보고한다. 신호가 전혀 없으면 `ok:false`이므로 배선을 확인한다.
- `rec <초>`(0.2~3초): 녹음을 IMA ADPCM(4:1, 2초 = 16KB)으로 줄여 `MIC_BEGIN … MIC_END` 줄로 보낸다. 블루투스로도 받을 수 있다.
- `voicecmd <키워드>`: `config.h`의 `VOICE_COMMANDS` 표에 있는 키워드만 실행한다. **모터 시험·아밍은 음성으로 할 수 없다.**

| 키워드 | 말 | 실행 |
|---|---|---|
| `stop` | 정지 / 멈춰 | `motorstop`, `msp off` (모터 시험 중지 + 보드 조종 해제) |
| `resume` | 다시 시작 | `msp on` |
| `status` | 상태 | `info` |
| `snap` | 사진 | `snap` |
| `mission_reset` | 미션 처음부터 | `mission reset` |

인식은 두 가지 모두 이 구조 위에 붙는다.

1. **PC 인식:** 보드가 녹음을 보내면, PC가 한국어로 알아듣고(예: Whisper) `voicecmd <키워드>`를 돌려보낸다.
2. **보드 인식:** 키워드별 녹음을 모아 작은 키워드 모델(TFLite Micro 등)을 학습시켜 보드에 넣는다. Espressif ESP-SR은 한국어를 지원하지 않는다.

```bash
python3 tools/voice_tool.py level                          # 소리 크기 확인
python3 tools/voice_tool.py rec --sec 2 --out a.wav        # 녹음 → WAV
python3 tools/voice_tool.py collect --keyword stop --count 20   # 학습 데이터 → dataset/stop/
python3 tools/voice_tool.py cmd status                     # 음성 명령 연결 시험
```

## 모니터 (D4D 폴더에서)

```bash
python3 tools/tag_monitor.py                  # 한글로 실시간 해석 (USB)
uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble   # 블루투스
python3 tools/tag_monitor.py --snap view.jpg  # 카메라 화면 한 장 저장
python3 tools/tag_monitor.py --cmd "tags"     # 명령 보내고 계속 모니터
python3 tools/tag_monitor.py --raw            # JSON 원문
```

포트를 열면 보드가 재부팅된다(ESP32-S3 내장 USB 특성).

| 시리얼 명령 | 동작 |
|---|---|
| `snap` | 현재 프레임을 JPEG(base64)로 전송 |
| `tags` | 등록된 태그 표(ID·크기·착륙점 오프셋) 출력 |
| `size <id> <mm>` | 그 ID의 검은 정사각형 한 변 길이 설정 (NVS 저장) |
| `land <id> <x> <y>` | 그 ID의 중심 → 착륙 지점 오프셋(mm) 설정. 인쇄물을 똑바로 놓고 오른쪽 +x, 위쪽 +y (NVS 저장) |
| `reset <id>` | 그 ID의 NVS 설정을 지우고 `config.h` 값으로 되돌림 |
| `dec auto` | decimate 자동 전환 (기본) |
| `dec <1.0~4.0>` | decimate 수동 고정. 재부팅하면 자동으로 돌아감 |
| `msp on` / `msp off` | FC로 MSP 송신 켜기/끄기. 끄면 INAV는 약 1초 안에 조종기 입력으로 돌아간다 |
| `mission reset` | 미션 단계를 '정렬'로 되돌림 |
| `simh <cm>` | 시뮬레이션 고도. 받는 동안 MTF-01P 대신 이 높이를 쓰고 `rc` 메시지를 낸다. 2초간 없으면 실제 센서로 복귀 (`sim/` 시뮬레이터가 사용) |
| `sim off` | 시뮬레이션 즉시 종료 |
| `mtfraw` | MTF-01P로 들어오는 다음 64바이트를 hex로 출력 (형식·배선 진단) |
| `info` | 현재 설정 출력 |

## 출력 메시지

| type | 주기 | 주요 필드 |
|---|---|---|
| `boot` / `info` | 부팅 시 / 명령 시 | build, camera_sensor, frame, tags_registered, decimate, dec_mode |
| `tags` | 부팅 시 / 명령 시 | 태그 표 (id, size_mm, land_x_mm, land_y_mm, meaning) |
| `flow` | 5Hz | 기존 RIDER-SERIAL과 같은 키: height_cm, forward/right/up_cm_s, quality, packets, errors + proto(micolink/msp), sim |
| `tag` | 인식한 태그마다 | id, registered, meaning, side_px, rot_deg, tag_size_mm, land_px, dist_est_cm, margin |
| `target` | 미션 태그가 보이는 프레임마다 | **판단 결과**: id, decision(`OVER_TARGET`/`MOVE`), move_ko, next_ko, body_right_cm, body_fwd_cm, height_cm, decimate |
| `msp` | 1Hz | enabled, fc_link, sent, acks, errors, out(보낸 채널 값), fc_rc(FC가 실제 쓰는 채널 값), phase |
| `rc` | 시뮬레이션 중 20Hz | ch(롤·피치·스로틀·요), seen, centered, phase, sim_h. 시뮬레이터에는 스로틀도 보낸다(실제 MSP는 `CTRL_THROTTLE_ENABLED`를 따름) |
| `vision` | 2Hz | fps, detect_ms, ids, target_id, decimate_used/next, dec_mode, dec_reason |

## 태그 표 (`src/config.h`의 `TAG_TABLE`)

| ID | 한 변 | 착륙점 오프셋 | 의미 |
|---|---|---|---|
| 0 | 100mm | (0, 0) | 미션 목표 지점. 노션 계획의 첫 시험 태그이며 저고도용 |
| 10 | 200mm | (0, 0) | 미션 목표 지점. 제작한 패널이며 고고도용 |
| 그 외 | 200mm 가정 | | `UNASSIGNED`: `tag` 줄만 나오고 `target` 판단에는 쓰지 않음 |

AprilTag 자체에는 ID 숫자만 들어 있고, 의미·크기·착륙 지점은 이 표에서 정한다.

### 중첩 태그 설정 예

작은 태그(ID 0, 100mm) 중심을 착륙 지점으로 정한다. 큰 태그(ID 10)를 그 오른쪽 170mm에 붙였다면 다음과 같이 설정한다.

```
land 0 0 0
land 10 -170 0     # ID 10 중심에서 왼쪽 170mm가 착륙 지점
```

- 멀리서는 큰 태그, 가까이서는 작은 태그가 보인다. 둘 다 보이면 화면에 더 크게 보이는 태그로 판단한다.
- 어느 태그로 판단하든 같은 착륙 지점을 가리킨다.
- 착륙 지점은 호모그래피로 투영하므로 패널이 회전하거나 기울어도 맞는다. PC 검증에서 회전 0°~250° 모두 두 태그의 결과 차이가 3mm 이내였다.
- 작은 태그는 큰 태그의 흰 여백 바깥에 붙인다. 태그끼리 겹치거나 흰 여백을 덮으면 인식되지 않는다.

## decimate 자동 전환

- **태그가 보일 때:** 목표 태그를 축소한 뒤 한 변이 20px 이상 남는 가장 큰 decimate를 쓴다. 더 큰 값으로 올릴 때는 24px 이상일 때만 올린다.
- **태그를 2프레임 연속 놓쳤을 때:** MTF-01P 높이로 가장 작은 미션 태그가 몇 px로 보일지 예측해 고른다. 4프레임마다 한 번은 2.0으로 넓게 찾는다.
- **높이 정보도 없을 때:** 2.0을 쓴다.

| 태그 | 4.0 (약 6fps) | 3.0 (약 4~5fps) | 2.0 (약 1.5~2.5fps) |
|---|---|---|---|
| 200mm | 약 1.3m 이내 | 약 1.7m 이내 | 그보다 멀 때 (실기 3.2m 인식 확인) |
| 100mm | 약 64cm 이내 | 약 85cm 이내 | 그보다 멀 때 |

(올라가는 기준. 내려가는 기준은 이보다 약 20% 먼 거리)

## FC 연결 (MSP)

### 배선: MicoAir H743 V2 UART5 커넥터

| 케이블 색 | H743 UART5 | 방향 | XIAO | 비고 |
|---|---|---|---|---|
| 검정 | GND | 공통 | GND | 필수 |
| 빨강 | 5V | FC → XIAO 전원 | 5V | **쇼트키 다이오드 경유** (줄무늬 쪽이 XIAO). 다이오드가 없으면 PC USB-C와 동시에 연결하지 말 것 |
| 노랑 | TX5 | FC → XIAO | **D1** (GPIO2) | TX↔RX 교차 |
| 초록 | RX5 | XIAO → FC | **D0** (GPIO1) | TX↔RX 교차 |

- 색상은 UART5 커넥터 핀 순서(GND·5V·TX5·RX5) 기준이다. 연결 전에 기판 인쇄로 노랑이 TX5인지 확인한다.
- **빨강(5V)은 반드시 XIAO의 `5V` 핀에만 연결한다.** 이 핀은 USB-C 5V와 같은 선이고, 보드 레귤레이터가 3.3V로 낮춘다. `3V3` 핀(출력 전용)이나 D0~D10(3.3V 신호, 5V 불가)에 연결하면 보드가 손상된다.
- 노랑·초록은 3.3V 신호(STM32H743)라 그대로 연결한다. MicoAir 문서에 신호 전압이 글로 명시돼 있지는 않으므로, 연결 전 FC에만 전원을 넣고 멀티미터로 확인한다: 빨강–검정 약 5V, 노랑–검정 약 3.3V, 초록–검정 3.3V 이하. 노랑이나 초록에서 5V가 나오면 연결하지 않는다.
- 연결 후 모니터에 `FC 응답 OK`가 나오면 노랑·초록이 맞게 연결된 것이다.

### INAV 설정

1. Ports 탭에서 UART5의 **MSP를 켠다** (115200).
2. Modes 탭에서 **MSP RC OVERRIDE**를 조종기 AUX 스위치에 연결한다. 이 스위치 채널은 INAV가 MSP 덮어쓰기에서 자동으로 제외한다.
3. CLI에서 `set msp_override_channels = 3` 후 `save`. 3은 롤(비트0)+피치(비트1)다.
   - 스로틀(비트2, 합계 7)은 `CTRL_THROTTLE_ENABLED=true`로 빌드하고 **ALTHOLD처럼 스로틀 중앙이 고도 유지인 모드**에서만 넣는다.
4. Channel Map이 AETR이 아니면 `config.h`의 `MSP_CH_*`를 같은 순서로 바꾼다.

### 보내는 값

| 상황 | 롤·피치 | 스로틀 |
|---|---|---|
| 착륙 지점이 3cm 이상 벗어남 | 1500 ± 4µs/cm, 최대 ±80µs | 1500 |
| 착륙 지점 위(3cm 이내) | 1500 | 1500 |
| 태그 안 보임·판단이 1초 넘게 없음 | 1500 (중립) | 1500 |
| 스로틀 제어를 켠 경우 | 위와 같음 | 단계 목표 고도(50/100cm)와의 차이 × 3µs/cm, 최대 ±60µs |

- 롤 +는 오른쪽, 피치 +는 앞쪽이다(INAV 스틱 관례).
- 기체 방향은 `CAM_ROTATE_DEG` 기준이며 아직 실측하지 않았다.

### 안전 동작 (INAV 소스 `rx/msp_override.c`, `rx/rx.c` 기준)

- 덮어쓰기는 **MSP RC OVERRIDE 모드가 켜져 있고**, `msp_override_channels`에 포함된 채널에만 적용된다.
- 보드가 멈추거나 `msp off`로 송신이 끊기면, 마지막 프레임부터 약 0.7~0.9초 안에 조종기 입력으로 돌아간다(`failsafe_delay` 기본 0.5초 + 0.2초). 그 사이에는 마지막 값을 유지한다.
- 스위치를 내리면 즉시 조종기 입력으로 돌아간다.

### 미션 단계

```
ALIGN ── 착륙 지점 3cm 이내 ──▶ GOTO_50 ── 50±5cm ──▶ HOLD_50 ── 누적 3초 ──▶ GOTO_100 ── 100±5cm ──▶ HOVER
```

- 태그를 놓치면 단계는 멈추고 명령은 중립이다.
- `mission reset`으로 처음 단계로 돌아간다.

### 첫 시험 순서 (프로펠러 제거)

1. INAV Receiver 탭을 띄우고 스위치를 켠 상태에서 패널을 움직여, 롤·피치 막대가 판단 방향대로 움직이는지 본다. 모니터의 `FC가 쓰는 값`으로도 확인할 수 있다.
2. 방향이 반대면 `CAM_ROTATE_DEG`를 고친다. 맞으면 `config.h`의 `AXES_VERIFIED`를 true로 바꾼다.
3. 스위치 내림, XIAO 전원 차단, `msp off`일 때 조종기 입력으로 돌아오는지 확인한다.

### PC 테스트

```bash
clang++ -std=c++17 -I src test/msp_host_test.cpp -o /tmp/msp_test && /tmp/msp_test | python3 test/check_msp.py
clang++ -std=c++17 -I src test/mtf_host_test.cpp -o /tmp/mtf_test && /tmp/mtf_test   # 실제 수신 바이트로 MTF 파서 시험
clang++ -std=c++17 -I src test/adpcm_host_test.cpp -o /tmp/adpcm_test && /tmp/adpcm_test | python3 test/check_adpcm.py   # 녹음 압축 왕복 (SNR 32dB)
```

## 판단 방식

- **수평 오프셋:** 태그 자신의 크기(ID별 mm ÷ 화면 속 픽셀 수)로 cm/px를 구해, 착륙 지점이 화면 중심에서 얼마나 떨어졌는지 계산한다. 카메라 캘리브레이션이 필요 없다.
- **정렬 판정:** 화면 중심에서 3cm 이내면 `OVER_TARGET`, 아니면 `MOVE`와 함께 이동 방향을 낸다.
- **다음 행동(미션 목표 태그일 때):** MTF-01P 높이를 기준으로 정한다. 50cm보다 높으면 하강, 45~55cm면 3초 유지 후 100cm로 상승, 그보다 낮으면 상승. 상태 기계 없이 현재 관측만으로 판단한다.
- **거리 추정(`dist_est_cm`):** 수평 화각 55° 기준이다. 20261003 실측에서 태그 304px일 때 MTF-01P 41cm와 일치했다.

## 미검증·제약

- 카메라 장착 방향과 기체 축 매핑은 검증하지 않았다(`axes_verified:false`). 장착 후 `CAM_ROTATE_DEG`를 맞춰야 한다.
- 200mm 태그는 약 45cm보다 가까우면 화면에 다 들어오지 않는다. 그보다 낮은 고도는 작은 태그(ID 0, 100mm)를 출력해 중첩 설정을 해야 한다. 중첩 전환은 PC 검증만 했고 실기는 아직이다.
- decimate 3.0·4.0으로 올라가는 전환은 실기에서 아직 보지 못했다. 시험 때 패널이 3.2m에 있어 2.0 유지가 맞는 상황이었다.
- MSP 송신은 FC 없이 PC 테스트(프레임 형식)와 보드 출력(채널 값)까지만 확인했다. 실제 FC 수신은 배선 후 확인해야 한다.
- 제어는 P 제어뿐이다. 기체가 기울면 카메라도 같이 기울어 화면이 흔들리는데, 이 보정은 없다. 이득은 프로펠러를 뗀 시험과 저고도 시험으로 조정해야 한다.

## 기존 펌웨어로 되돌리기

교체 전 8MB 전체를 `D4D/firmware-backup/`에 백업해 두었다(SHA256은 `SHA256SUMS.txt` 참고). D4D 폴더에서 실행한다.

```bash
python3 ~/.platformio/packages/tool-esptoolpy/esptool.py --port /dev/cu.usbmodem2101 \
  write_flash 0 firmware-backup/RIDER-SERIAL-20260924-06_full8MB.bin
```

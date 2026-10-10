# 03. 배선

모든 신호는 3.3V다. TX(보내기)는 상대의 RX(받기)에 교차해서 연결하고, GND는 항상 공통으로 묶는다.

## FC 포트 배정 (2026-10-10, 사용자 확인)

| FC 포트 | 연결 | INAV 설정 |
|---|---|---|
| UART4 | **MTF-01P** (거리·옵티컬플로우) | MSP, `rangefinder_hardware = MSP`, `opflow_hardware = MSP` |
| UART5 | **XIAO ESP32S3 Sense** (태그인식 보드) | MSP |
| UART6 (커넥터 표기 SBUS/CRSF) | **FS-A8S V2 수신기** (iBUS) | 시리얼 수신기, `serialrx_provider = IBUS` |
| ESC 커넥터 (8핀) | 4-in-1 ESC | DShot300, 모터 M1~M4 |
| UART1·UART2·UART3·UART7·UART8 | 비어 있음 | 꺼짐 |
| LED·BUZZER, GPS 커넥터 | 비어 있음 (10-10 사진) | — 카메라 서보 전원(5V)으로 쓸 수 있음 |

## 1) XIAO ↔ FC (UART5 커넥터)

UART5 커넥터는 4핀(GND · 5V · TX5 · RX5)이다. 출처는 [제조사 핀 배치도](https://micoair.cn/en/docs/flight-controller/micoair743/micoair743v2-flight-controller-manual)다.

| FC UART5 | 신호 방향 | XIAO | 케이블 색 (사용자 기준) |
|---|---|---|---|
| GND | 공통 | GND | 검정 |
| 5V | FC → XIAO 전원 | **5V 핀** (다이오드 경유 권장) | 빨강 |
| TX5 (FC가 보냄) | FC → XIAO | **D1** (GPIO2) | 노랑 *(아래 주의)* |
| RX5 (FC가 받음) | XIAO → FC | **D0** (GPIO1) | 초록 *(아래 주의)* |

> **확인 필요:** 처음에는 "노랑 = TX5 → D1, 초록 = RX5 → D0"으로 연결했지만 FC가 응답하지 않았다. XIAO 쪽에서 노랑과 초록을 **서로 바꾼 뒤** 정상 통신됐다. 실제 케이블은 색과 기능이 반대일 수 있다. 기능 기준(FC TX5 → D1, FC RX5 → D0)을 따르고, 실제 색은 다시 확인해 이 표를 고친다.

- INAV 설정: Ports 탭에서 UART5의 **MSP를 켜야** 응답한다(115200).
- UART5 바로 옆에 같은 모양의 UART4 커넥터가 있다(지금은 MTF-01P). 바뀌지 않게 주의한다.

## 2) MTF-01P ↔ FC (UART4 커넥터)

2026-10-10부터 MTF-01P는 FC UART4에 연결돼 있다(MicoAir 공식 배선도와 같은 방식).

| MTF-01P | FC UART4 |
|---|---|
| GND | GND |
| 5V | 5V |
| RX | TX4 |
| TX | RX4 |

- FC가 거리센서·옵티컬플로우를 정상으로 받는다(10-10 `status`: RANGEFINDER=OK, OPFLOW=OK).
- 이전에 연결돼 있던 XIAO D7에는 데이터가 들어오지 않는다(0바이트). 보드는 높이를 FC에서 MSP로 읽는다(`-21`, [06](06-sensor-mtf01p.md)).

## 3) FC ↔ ESC (8핀 커넥터)

FC 한쪽 가장자리에 **모양이 같은 8핀 커넥터가 두 개** 있다. 제조사 핀 배치도와 10-10 사진으로 확인했다.

| 커넥터 | 핀 순서 (제조사 그림 기준 왼쪽 → 오른쪽) | 인쇄 표기 |
|---|---|---|
| **ESC 포트** | Curr, RX7, **M4, M3, M2, M1**, VBAT, GND | "ESC", `C R 4 3 2 1 V G` |
| 확장 포트 | M10, M9, M8, M7, M6, M5, VBAT, GND | 없음 |

- 두 커넥터는 **VBAT·GND 위치가 같다.** 그래서 ESC 케이블을 확장 포트에 잘못 꽂아도 FC 전원은 들어오지만, 모터 신호는 ESC에 가지 않는다.
- **10-10 사진 확인:** ESC 케이블은 올바른 ESC 포트(`CR4321VG`)에 꽂혀 있다. 확장 포트는 비어 있다.
- **INAV 출력 핀:** 타깃 `MICOAIR743V2`(9.1.0 `target.c`)는 S1~S4 = TIM1 CH4·CH3·CH2·CH1 = PE14·PE13·PE11·PE9이다. 제조사 표의 M1~M4와 같다.
- MicoAir 공식 배선도의 ESC(MicoAir 4IN1)는 같은 순서(Curr, Tx, M4~M1, VBAT, GND)라 케이블이 1:1이다. 다른 회사 ESC라면 ESC 쪽 핀 순서를 따로 확인해야 한다.

## 4) XIAO ↔ INMP441 마이크

| INMP441 | XIAO | 비고 |
|---|---|---|
| VDD | 3V3 | 3.3V 전용 |
| GND | GND | |
| SCK (BCLK) | D3 (GPIO4) | 보드가 클록을 냄 |
| WS (LRCLK) | D4 (GPIO5) | 보드가 클록을 냄 |
| SD | D5 (GPIO6) | 마이크 → 보드 |
| L/R | GND | 왼쪽 채널. 펌웨어가 소리 있는 채널을 자동 선택 |

## 5) 카메라 팬·틸트 서보 (예정, `-21`)

| 서보 | 신호 | 전원 | GND |
|---|---|---|---|
| 팬 (아래쪽, 수직축) | XIAO **D8** (GPIO7) | FC 5V (LED·BUZZER 또는 GPS 커넥터의 5V) | 공통 |
| 틸트 (위쪽) | XIAO **D9** (GPIO8) | 같음 | 공통 |

- 서보 전원을 **XIAO 3V3에서 따면 안 된다.** 서보는 움직일 때 수백 mA를 쓴다.
- FC 5V BEC는 최대 3A이고, XIAO·수신기·MTF-01P와 함께 쓴다. 영상이나 보드가 흔들리면 서보 쪽 5V에 470µF 전후 커패시터를 단다.
- 신호는 3.3V지만 대부분의 서보가 인식한다.
- 자세한 내용은 [13](13-camera-pan-tilt.md)에 있다.

## 전압 주의

| 선 | 전압 | 꽂아도 되는 곳 | 절대 안 되는 곳 |
|---|---|---|---|
| FC 5V (빨강) | 5V | XIAO **5V 핀**, 서보 전원 | XIAO 3V3 핀, D0~D10 |
| FC TX5·RX5 | 3.3V | XIAO D0·D1 | — |
| XIAO 3V3 | 3.3V 출력 | INMP441 VDD | 외부 전원 입력·서보 전원으로 사용 금지 |

- 연결 전에 FC에만 전원을 넣고 멀티미터로 잰다. 빨강–검정은 약 5V, 신호선–검정은 3.3V 이하여야 한다. 신호선에서 5V가 나오면 연결하지 않는다.
- XIAO에 USB-C를 꽂을 때, 5V 선에 다이오드가 없다면 빨간 선을 먼저 뺀다.

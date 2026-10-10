# 03. 배선

모든 신호는 3.3V다. TX(보내기)는 상대의 RX(받기)에 교차해서 연결하고, GND는 항상 공통으로 묶는다.

## 1) XIAO ↔ FC (MicoAir H743 V2, UART5 커넥터)

UART5 커넥터는 4핀(GND · 5V · TX5 · RX5)이다. 출처는 [제조사 핀 배치도](https://micoair.cn/en/docs/flight-controller/micoair743/micoair743v2-flight-controller-manual)다.

| FC UART5 | 신호 방향 | XIAO | 케이블 색 (사용자 기준) |
|---|---|---|---|
| GND | 공통 | GND | 검정 |
| 5V | FC → XIAO 전원 | **5V 핀** (다이오드 경유 권장) | 빨강 |
| TX5 (FC가 보냄) | FC → XIAO | **D1** (GPIO2) | 노랑 *(아래 주의)* |
| RX5 (FC가 받음) | XIAO → FC | **D0** (GPIO1) | 초록 *(아래 주의)* |

> **확인 필요:** 처음에는 "노랑 = TX5 → D1, 초록 = RX5 → D0"으로 연결했지만 FC가 응답하지 않았다. XIAO 쪽에서 노랑과 초록을 **서로 바꾼 뒤** 정상 통신됐다. 실제 케이블은 색과 기능이 반대일 수 있다. 기능 기준(FC TX5 → D1, FC RX5 → D0)을 따르고, 실제 색은 다시 확인해 이 표를 고친다.

- INAV 설정: Ports 탭에서 UART5의 **MSP를 켜야** 응답한다(115200). 처음에는 꺼져 있었다(`serial 4 0 …`).
- UART5 바로 옆에 같은 모양의 UART4 커넥터가 있으니 바뀌지 않게 주의한다.

## 2) XIAO ↔ MTF-01P

| MTF-01P | XIAO | 비고 |
|---|---|---|
| TX | D7 (GPIO44) | 보드가 받음 |
| RX | D6 (GPIO43) | 보드가 보냄 (현재 쓰지 않음) |
| GND | GND | |
| 전원 | 기록 없음 | 전원을 어디서 받는지 확인해 기록할 것 |

통신은 115200bps, 100Hz다. 출력 형식(Micolink·MSP)은 [06](06-sensor-mtf01p.md)에 있다.

## 3) XIAO ↔ INMP441 마이크

| INMP441 | XIAO | 비고 |
|---|---|---|
| VDD | 3V3 | 3.3V 전용 |
| GND | GND | |
| SCK (BCLK) | D3 (GPIO4) | 보드가 클록을 냄 |
| WS (LRCLK) | D4 (GPIO5) | 보드가 클록을 냄 |
| SD | D5 (GPIO6) | 마이크 → 보드 |
| L/R | GND | 왼쪽 채널. 펌웨어가 소리 있는 채널을 자동 선택 |

## 4) FC 쪽 다른 연결 (참고)

| 연결 | FC 단자 | 비고 |
|---|---|---|
| FS-A8S V2 수신기 iBUS | UART6 (RX6) · 5V · GND | 수신기 PPM 선은 쓰지 않음 (TX6에 연결 금지) |
| BLS 50A 4-in-1 ESC | 모터 출력 S1~S4 → M1~M4, VBAT, GND, CURRENT | ESC 텔레메트리 없음, RX7은 비워 둠 |

## 전압 주의

| 선 | 전압 | 꽂아도 되는 곳 | 절대 안 되는 곳 |
|---|---|---|---|
| FC 5V (빨강) | 5V | XIAO **5V 핀** | XIAO 3V3 핀, D0~D10 |
| FC TX5·RX5 | 3.3V | XIAO D0·D1 | — |
| XIAO 3V3 | 3.3V 출력 | INMP441 VDD | 외부 전원 입력으로 사용 금지 |

- 연결 전에 FC에만 전원을 넣고 멀티미터로 잰다. 빨강–검정은 약 5V, 신호선–검정은 3.3V 이하여야 한다. 신호선에서 5V가 나오면 연결하지 않는다.
- XIAO에 USB-C를 꽂을 때, 5V 선에 다이오드가 없다면 빨간 선을 먼저 뺀다.

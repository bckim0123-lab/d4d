# 02. XIAO ESP32S3 Sense 제원

출처 표기: **[실측]** = 이 보드에서 esptool·펌웨어로 직접 확인한 값, **[Seeed]** = [Seeed 공식 위키](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/), **[빌드]** = PlatformIO 보드 정의.

## 칩·메모리·무선

| 항목 | 값 | 출처 |
|---|---|---|
| 칩 | ESP32-S3 (revision v0.2), 듀얼코어, 240MHz | [실측] esptool, [빌드] |
| 크리스털 | 40MHz | [실측] |
| 플래시 | 8MB, quad (제조사 0xC8, 장치 0x4017) | [실측] esptool `flash_id` |
| PSRAM | 8MB (OPI). 펌웨어 동작 중 여유 약 6.5MB | [실측] 부팅 메시지 |
| 내부 RAM | 약 320KB. 펌웨어 동작 중 여유 약 115KB, 최저 약 82~91KB | [실측] `info` |
| 무선 | Wi-Fi(2.4GHz), **BLE만** 지원 (Bluetooth Classic·SPP 없음) | [실측] esptool `Features: WiFi, BLE` |
| MAC | 74:4D:BD:81:B8:B4 | [실측] |
| USB | 칩 내장 USB-Serial/JTAG (VID 0x303a, PID 0x1001), 12Mb/s | [실측] |
| 파티션 | default_8MB (앱 영역 3.3MB) | [빌드] |

## 전원

| 핀 | 설명 | 출처 |
|---|---|---|
| 5V | "USB 포트에서 나오는 5V. 전압 입력으로도 쓸 수 있지만 외부 전원과 이 핀 사이에 다이오드를 두어야 한다(애노드 → 외부 전원, 캐소드 → 5V 핀)" | [Seeed] |
| 3V3 | 보드 레귤레이터 출력, 최대 700mA. **입력으로 쓰면 안 됨** | [Seeed] |
| 입력 전압 | USB-C 5V, 배터리(BAT 패드) 3.7V | [Seeed] |
| GPIO | 3.3V 신호. **5V를 넣으면 안 됨** | ESP32-S3 공통 |

- 지금은 FC 5V(빨간 선)를 XIAO **5V 핀**에 넣어 전원을 받는다. 다이오드가 있는지는 확인되지 않았다.
- 다이오드가 없으면 USB-C를 꽂기 전에 빨간 선을 빼야 한다. FC 5V와 PC USB 5V가 서로 맞물리기 때문이다.

## 핀 사용 현황

| 핀 | GPIO | 사용처 | 방향 |
|---|---|---|---|
| D0 | 1 | FC UART5 RX5로 (MSP 송신) | 출력 |
| D1 | 2 | FC UART5 TX5에서 (MSP 수신) | 입력 |
| D2 | 3 | 비어 있음 (스트래핑 관련이라 피함) | — |
| D3 | 4 | INMP441 SCK (I2S 비트 클록) | 출력 |
| D4 | 5 | INMP441 WS (좌우 채널 클록) | 출력 |
| D5 | 6 | INMP441 SD (음성 데이터) | 입력 |
| D6 | 43 | MTF-01P RX로 | 출력 |
| D7 | 44 | MTF-01P TX에서 | 입력 |
| D8 | 7 | 비어 있음 (SD 카드를 쓰면 SCK) | — |
| D9 | 8 | 비어 있음 (SD 카드를 쓰면 MISO) | — |
| D10 | 9 | 비어 있음 (SD 카드를 쓰면 MOSI) | — |
| 5V | — | FC 5V 입력 (또는 USB) | 전원 |
| 3V3 | — | INMP441 VDD | 전원 출력 |
| GND | — | FC·MTF-01P·INMP441 공통 접지 | — |

**Sense 확장 보드가 쓰는 내부 핀 (헤더에 없음)**

| 기능 | GPIO |
|---|---|
| 카메라 | XCLK 10, SIOD 40, SIOC 39, D7~D0(Y9~Y2) 48·11·12·14·16·18·17·15, VSYNC 38, HREF 47, PCLK 13 |
| 내장 PDM 마이크 (사용 안 함) | CLK 42, DATA 41 |
| microSD (사용 안 함) | CS 21 + D8~D10 |

## 주변장치 할당 (펌웨어 기준)

| 주변장치 | 용도 |
|---|---|
| UART1 (`Serial1`) | MTF-01P, 115200 |
| UART2 (`Serial2`) | FC MSP, 115200 |
| USB-Serial/JTAG (`Serial`) | PC 연결 (상태 출력·명령·업로드) |
| I2S0 | INMP441 마이크 (카메라는 LCD_CAM을 쓰므로 겹치지 않음) |
| LCD_CAM | OV2640 카메라 |
| BLE | Nordic UART Service (`TagBoard-B8B4`) |

## USB 연결 시 주의

- **시리얼 포트를 열면 보드가 재부팅될 수 있다.** 로그에는 `rst:0x15 (USB_UART_CHIP_RESET)`로 남는다. 블루투스 연결은 재부팅을 일으키지 않는다.
- arduino-esp32 2.0.17의 USB 시리얼(HWCDC)은 USB 인터럽트가 도는 코어(core 1)가 아닌 곳에서 쓰면 64바이트 패킷 일부를 잃는다. 그래서 펌웨어는 모든 출력을 core 1의 출력 태스크 하나로 모은다.
- 아무도 USB를 읽지 않으면 전송 버퍼가 차므로, 펌웨어는 그 줄을 기다리지 않고 건너뛴다. 블루투스 출력이 막히지 않게 하기 위해서다.

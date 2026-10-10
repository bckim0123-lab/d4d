# 08. 블루투스 (BLE)

ESP32-S3는 BLE만 지원한다(Bluetooth Classic·SPP 없음). 그래서 Mac에 COM 포트로 잡히지 않는다. 대신 **Nordic UART Service**로 USB 시리얼과 같은 줄 단위 텍스트를 주고받는다.

## 서비스

| 항목 | 값 |
|---|---|
| 광고 이름 | `TagBoard-XXXX` (MAC 끝 2바이트, 이 보드는 **`TagBoard-B8B4`**) |
| 서비스 UUID | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` |
| RX (PC → 보드, 쓰기) | `6E400002-…` 명령 한 줄 + `\n` |
| TX (보드 → PC, 알림) | `6E400003-…` USB와 같은 JSON 줄 |
| MTU | 247 (Mac과 협상된 값) |
| 속도 | 약 7KB/s. 상태 줄(2~4KB/s)은 여유 있음, 스냅샷 30KB는 약 4초 |

- USB와 동시에 쓸 수 있다. 연결해도 보드가 재부팅되지 않는다.
- 보드를 USB 없이 FC 5V로 켜고 PC와는 블루투스로만 통신할 수 있다.
- 라이브러리는 NimBLE-Arduino 1.4.3이다. BLE 전송 버퍼는 빌드 옵션으로 24개로 늘렸다.

## 사용법

```bash
# 모니터 (bleak 필요)
uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble
uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble --snap view.jpg
uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble --cmd "info"
```

- 브라우저 시뮬레이터에서는 **블루투스 연결** 버튼을 누른다(Chrome Web Bluetooth).
- 목록이 비어 있으면 macOS 시스템 설정 → 개인정보 보호 → 블루투스에서 Chrome이나 터미널을 허용한다.

## 안정화하며 찾은 문제 (되돌리지 말 것)

카메라가 꺼져 있을 때는 문제가 없었다. 카메라와 AprilTag가 동작하자 다음 문제가 드러났다.

| 증상 | 원인 (로그로 확인) | 조치 |
|---|---|---|
| 연결 직후나 몇 초 뒤 데이터가 멈추고 끊김. 로그 `assert llc_con_upd.c 488` | 연결 직후 보드가 연결 간격·DLE·PHY 변경을 요청하면, 같은 시점에 시작된 macOS 쪽 갱신과 겹쳐 BLE 컨트롤러가 망가짐 | 연결 직후 파라미터 요청을 하지 않음 |
| 카메라가 켜지면 연결 실패. 로그 `BLE_INIT: Malloc failed` | AprilTag가 프레임마다 작은 할당을 수천 번 해서 내부 RAM 여유가 최저 **324바이트**까지 떨어짐 | `heap_caps_malloc_extmem_enable(32)`로 검출 중 할당을 PSRAM으로 보냄 → 최저 약 **91KB**, 검출 속도 변화 없음 |
| 알림 일부 누락, 연속 전송 중 정지 | 전송 버퍼를 몰아 써서 BLE 스택이 쓸 몫이 남지 않음 | 버퍼 24개 중 8개는 스택 몫으로 남김, 알림 사이 최소 4ms |
| 한 줄이 중간에 끊겨 다음 줄과 붙음 | 시간 초과로 줄을 보내다가 포기함 | 시작 전에만 포기, 보내기 시작한 줄은 끝까지. 그래도 실패하면 줄바꿈만 보내 분리 |
| USB 스냅샷 일부 누락 | USB 버퍼에 자리가 없으면 그 줄을 건너뜀 | PC가 읽는 중이면 최대 40ms 기다림, 아무도 안 읽으면 건너뜀 |

## 확인 기록

| 날짜 | 시험 | 결과 |
|---|---|---|
| 2026-10-03 | 카메라 꺼진 상태 첫 연결 | 4초 만에 발견, MTU 247, 깨진 줄 0, `simh`·`info` 명령 처리, `rc` 20Hz |
| 2026-10-03 | 수정 후 스냅샷 5번 연속 (카메라 켬) | 5번 모두 온전 (base64 길이 일치), 장당 4.1~4.4초, 누락 0 |
| 2026-10-03 | FC 전원만으로 켠 상태 | 연결 정상, 신호 −35~−55dBm, FC MSP 상태까지 블루투스로 확인 |

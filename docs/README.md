# 자율드론 태그인식 보드 — 프로젝트 문서

기준일: 2026-10-10 (처음 작성 2026-10-03)

XIAO ESP32S3 Sense 보드로 바닥의 AprilTag를 인식하고, 판단한 이동 명령을 FC(MicoAir H743 V2, INAV)에 MSP로 보내는 프로젝트다. 이 폴더는 하드웨어 제원, 배선, 펌웨어, 시험 기록, 남은 과제를 정리한다.

## 현재 상태 한눈에

| 영역 | 상태 | 비고 |
|---|---|---|
| 보드 펌웨어 | `TAGVISION-20261010-20` 동작 중 | `-21`(FC 높이 사용, 카메라 팬·틸트)은 코드 작성 중. 빌드·업로드 전 |
| 카메라·AprilTag | 동작 확인 | ID 10(200mm) 패널을 약 0.4~3.2m에서 인식 |
| MTF-01P | **FC UART4로 옮김** | FC가 거리·플로우를 정상으로 받음. 보드에는 더 이상 직접 들어오지 않음 → `-21`부터 FC에서 높이를 읽음 |
| FC 통신 (MSP, UART5) | 동작 확인 | 보드가 FC 상태(아밍 차단 사유·전압·전류·모터 출력)도 읽음 |
| **킬(정지) 기능** | **동작 확인 (프로펠러 없이)** | 보드 `kill` → INAV FAILSAFE(DROP) → 즉시 디스아밍. ch6가 보드 조종 쪽일 때만 동작 |
| 모터 | **돌지 않음 — 원인을 ESC 쪽으로 좁힘** | FC 설정·출력 핀·커넥터·출력값은 모두 정상. ESC가 신호에 반응하지 않음 |
| 블루투스 (BLE) | 동작 확인 | `TagBoard-B8B4`. 10-10 시험은 대부분 BLE로 진행 (USB 출력이 멈춤) |
| 마이크(INMP441) | 펌웨어에 포함(`-19`부터), 실기 시험 전 | 배선 완료 |
| 카메라 팬·틸트 | 설계·코드 작성 중 | 서보 2개(D8·D9). 서보·브래킷 미장착 |
| 브라우저 시뮬레이터 | 헤드리스 검증만 | 실제 보드로는 아직 실행 안 함 |

## 문서 목록

| 문서 | 내용 |
|---|---|
| [01. 시스템 개요](01-system-overview.md) | 목표, 전체 구성도, 부품, 진행 경과 |
| [02. XIAO ESP32S3 Sense 제원](02-xiao-esp32s3-sense.md) | 칩·메모리·전원·핀 사용 현황·USB 특성 |
| [03. 배선](03-wiring.md) | FC 포트 배정, ESC 커넥터, 보드 ↔ FC·마이크·서보 연결, 전압 주의 |
| [04. 펌웨어](04-firmware.md) | 구조, 빌드·업로드·복구, 설정값, 명령, 출력 메시지, 버전 이력 |
| [05. AprilTag 인식](05-vision-apriltag.md) | 태그 표, 판단 방식, decimate 자동 전환, 성능, 한계 |
| [06. MTF-01P 센서](06-sensor-mtf01p.md) | 제원, 현재 연결(FC UART4), 출력 형식, 보드가 높이를 받는 방법 |
| [07. FC 연동 (MSP)](07-fc-msp-integration.md) | FC 설정(10-10), 상태 읽기, 킬 기능, MSP RC Override, 미션 단계, 모터 시험 |
| [08. 블루투스](08-bluetooth.md) | BLE 서비스, 사용법, 안정화하며 찾은 문제 |
| [09. 마이크·음성 명령](09-voice-mic.md) | INMP441, 녹음, 음성 명령 구조와 계획 |
| [10. 도구·시뮬레이터](10-tools-simulator.md) | 모니터·음성 도구, FC 직접 점검, 브라우저 시뮬레이터, PC 테스트 |
| [11. 시험 기록](11-test-log.md) | 날짜별 시험과 측정값 |
| [12. 남은 과제·안전 수칙](12-issues-next-steps.md) | 미해결 문제, 다음 할 일, 안전 수칙 |
| [13. 카메라 팬·틸트](13-camera-pan-tilt.md) | 운행 중 카메라 방향 전환: 하드웨어 안, 각도 규약, 위치 계산, 명령 |

## 프로젝트 폴더

```
D4D/
├── docs/              이 문서들
├── tag-board-fw/      보드 펌웨어 (PlatformIO) — 자세한 사용법은 tag-board-fw/README.md
├── tools/             tag_monitor.py(모니터), voice_tool.py(마이크)
├── sim/               브라우저 시뮬레이터 (index.html)
├── fc-info/           FC 설정 원본. diff_all_20261010.txt = 10-10 변경 전 복원용 백업 (diff_all.txt는 10-03 백업)
└── firmware-backup/   교체 전 보드 펌웨어 8MB 백업
```

## 외부 자료

- 계획서 (노션): [20260930 수 | 자율드론 태그인식 보드 부품 목록 및 연결·검증 계획](https://app.notion.com/p/20260930-3eb439d14c94819680c8d1de9b3ef9eb)
- FC 정보 정리 문서 (10-03 기준, 10-10 변경 사항은 [07](07-fc-msp-integration.md)이 최신): [드론 FC 정보 정리 · MicoAir H743 V2 (INAV)](https://claude.ai/code/artifact/1369af2b-fbe6-4b09-942e-8c66aa56f68b)
- FC 제조사 자료: [MicoAir743 V2 매뉴얼](https://micoair.cn/en/docs/flight-controller/micoair743/micoair743v2-flight-controller-manual), [제품 페이지(핀 배치도·배선도)](https://micoair.com/flightcontroller_micoair743v2/)
- INAV 타깃 소스: [MICOAIR743V2 target.c (9.1.0)](https://github.com/iNavFlight/inav/blob/9.1.0/src/main/target/MICOAIR743V2/target.c)

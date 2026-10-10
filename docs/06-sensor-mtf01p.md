# 06. MTF-01P 센서 (MicoAir)

레이저 거리센서와 옵티컬플로우를 합친 센서다. 노션 계획서에서 최종 지정한 센서다.

> **2026-10-10 현재: FC UART4에 연결돼 있다.** 태그인식 보드(D6·D7)에는 데이터가 들어오지 않는다(0바이트 확인). FC는 INAV `rangefinder_hardware = MSP`, `opflow_hardware = MSP`로 받고 있고, `status`에서 RANGEFINDER=OK, OPFLOW=OK였다. 보드는 높이를 FC에서 MSP로 읽는다(아래 "보드가 높이를 받는 방법").

## 제원 ([제조사 페이지](https://micoair.com/optical_range_sensor_mtf-01p/))

| 항목 | 값 |
|---|---|
| 출력 형식 | Micolink(자체), MSP(INAV용), MAVLink(APM·PX4) — 설정으로 선택 |
| 통신 | UART 115200, LVTTL 3.3V |
| 출력 주기 | 100Hz |
| 플로우 화각 | 42° |
| 최대 측정 속도 | 7m/s (1m 높이 기준) |
| 거리 | 12m (반사율 90%, 600lux) / 8m (90%, 60klux) |
| 거리 정확도 | 2cm (0.1~2m) / 2% (2m 이상) |

## 현재 출력 형식: **MSP (INAV용)**

처음에는 Micolink이었다. 2026-10-03 오후 센서 설정이 MSP로 바뀐 것을 원시 바이트(`mtfraw`)로 확인했다. 10-10에 실제로 FC UART4로 옮겨졌다. 펌웨어 `-06`부터는 두 형식을 자동으로 구분하므로 어느 쪽이든 동작한다(`flow` 메시지의 `proto` 값).

### Micolink ([디코딩 문서](https://micoair.com/docs/decoding-micolink-messages-from-mtf-01/))

```
0xEF | dev_id | sys_id | msg_id | seq | len | payload[len] | checksum
checksum = 0xEF부터 payload 끝까지 바이트 합(uint8). 패킷 27바이트(len 20)
```

| msg 0x51 payload | 형식 | 단위 |
|---|---|---|
| time_ms | u32 | ms |
| distance | u32 | mm (0 = 측정 불가) |
| strength · precision · dis_status · 예약 | u8 ×4 | dis_status 1 = 유효 |
| flow_vel_x · flow_vel_y | i16 ×2 | cm/s (1m 높이 기준) |
| flow_quality · flow_status | u8 ×2 | |
| 예약 | u16 | |

실제 속도(cm/s) = flow_vel × 높이(m)

### MSP v2 (INAV용 설정)

```
'$' 'X' '<' | flag | cmd(LE16) | size(LE16) | payload | crc8_dvb_s2(flag~payload)
```

| cmd | payload | 비고 |
|---|---|---|
| 0x1F01 거리 | quality u8, distanceMm i32 | 음수 = 범위 밖 |
| 0x1F02 플로우 | quality u8, motionX i32, motionY i32 | 메시지 사이에 쌓인 이동량 |

INAV와 같은 계산으로 바꾼다(INAV `sensors/opflow.c`, 이 FC의 `opflow_scale` = 10.5).

```
각속도(°/s) = motion ÷ 메시지 간격(s) ÷ opflow_scale
속도(cm/s, 1m 기준) = 각속도(rad/s) × 100   → 실제 속도 = × 높이(m)
```

실제로 받은 바이트 예시:
```
24 58 3c 00 01 1f 05 00 | 34 | ad 06 00 00 | cf   → 거리 0x06AD = 1709mm, 품질 0x34
```

## 보드가 높이를 받는 방법 (`-21`)

| 순서 | 출처 | 조건 | `height_src` |
|---|---|---|---|
| 1 | 시뮬레이터 가상 고도 (`simh`) | 시뮬레이션 중 | `sim` |
| 2 | MTF-01P 직결 (D7) | 300ms 안에 패킷이 있음 | `mtf` |
| 3 | **FC 거리센서** (`MSP_SONAR_ALTITUDE` 58, 10Hz) | 300ms 안에 응답이 있음 | `fc` |

- INAV 소스(9.1.0 `fc_msp.c`, `sensors/rangefinder.c`) 기준으로, FC가 주는 값은 `rangefinder.calculatedAltitude`다. 원시 거리 × cos(기울기)를 한 cm 값이고, 범위 밖이면 −1이다. 보드는 0 이하를 "높이 없음"으로 처리한다.
- FC 출처일 때는 높이만 있고 플로우 속도는 없다(`flow` 메시지의 속도 값은 null). 속도와 위치 유지는 FC가 처리한다.
- 10-10 확인: FC에 `fcmsp 58`로 물었을 때 3cm였다(기체가 작업대 위에 놓인 상태).

## 보드에서의 처리 (MTF-01P 직결일 때)

| 항목 | 내용 |
|---|---|
| 수신 | `flowTask`(core 0)가 2ms마다 읽어 파싱, 300ms 동안 패킷이 없으면 `connected:false` |
| 출력 | `flow` 5Hz. 키는 원래 RIDER-SERIAL 펌웨어와 같다 (x → 앞, y → 오른쪽) |
| 상승 속도 | 거리 차분을 저역통과해 `up_cm_s`로 낸다 (잡음이 큼) |
| 시뮬레이션 | `simh` 동안 높이만 가상 값으로 바꾼다 |

## 측정 기록

| 상황 | 값 |
|---|---|
| 원래 펌웨어 (Micolink) | 약 90패킷/s, 오류 0. 천장 쪽 거리 172~175cm. 정지 상태에서 raw 0 또는 ±20(±34.7cm/s)으로 튀는 잡음 |
| 새 펌웨어 (MSP 모드) | 8초간 연결 유지, 오류 0, 약 185메시지/s(거리+플로우) |

## 확인할 것

- MSP 모드 플로우 속도의 실제 단위는 움직여 보며 검증하지 않았다.
- 축 방향(앞·오른쪽)을 검증하지 않았다.
- 센서는 FC로 옮겼다(10-10). FC 모드 목록에 NAV POSHOLD가 생겼는지 다시 조회해야 한다([12](12-issues-next-steps.md)).
- `-21`을 올린 뒤 `flow` 메시지가 `height_src:"fc"`와 FC 높이를 내는지 확인한다.

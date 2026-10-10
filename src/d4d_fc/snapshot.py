"""오프라인 개발용 전체 스냅샷 수집.

케이블을 뽑은 뒤에도 개발·테스트를 계속할 수 있도록, FC의 응답을 원본 바이트까지
전부 저장한다. 저장된 스냅샷은 replay.ReplayClient로 되살려 실제 장치처럼 쓸 수 있다.

안전 원칙: SAFE_GET에 명시된 조회 명령만 보낸다. 허용목록에 없는 코드는 코드 차원에서
차단되므로, 캘리브레이션 시작(205/206), 설정 초기화(208), EEPROM 기록(250),
재부팅(68), 플래시 소거(72) 같은 명령이 실수로 나갈 수 없다.
"""
from __future__ import annotations

import struct
import time
from typing import Any, Callable

from . import codes as C
from . import decode as D
from .msp import MspError

# ---------------------------------------------------------------- 허용/차단

# 값을 바꾸거나 동작을 일으키는 명령. 절대 보내지 않는다.
NEVER_SEND = {
    35, 37, 39, 41, 43, 45, 47, 49, 51, 53, 55, 57, 60, 62, 65, 67,
    68,   # MSP_REBOOT
    72,   # MSP_DATAFLASH_ERASE
    74, 76, 78, 81, 83, 85, 87, 89, 97,
    200, 201, 202, 203, 204,
    205,  # MSP_ACC_CALIBRATION
    206,  # MSP_MAG_CALIBRATION
    207,
    208,  # MSP_RESET_CONF
    209, 210, 211, 212, 214, 215, 216, 217, 218, 219, 220, 221, 222,
    250,  # MSP_EEPROM_WRITE
    0x1002, 0x1004, 0x1006, 0x100A,
    0x2004, 0x2006, 0x2008, 0x200C, 0x2011, 0x2013,
} | C.WRITE_COMMANDS

# 조회 전용 명령. (이름, 코드, 요청 payload)
SAFE_GET: list[tuple[str, int, bytes]] = [
    ("MSP_API_VERSION", 1, b""),
    ("MSP_FC_VARIANT", 2, b""),
    ("MSP_FC_VERSION", 3, b""),
    ("MSP_BOARD_INFO", 4, b""),
    ("MSP_BUILD_INFO", 5, b""),
    ("MSP_NAME", 10, b""),
    ("MSP_WP_GETINFO", 20, b""),
    ("MSP_MODE_RANGES", 34, b""),
    ("MSP_FEATURE", 36, b""),
    ("MSP_BOARD_ALIGNMENT", 38, b""),
    ("MSP_CURRENT_METER_CONFIG", 40, b""),
    ("MSP_MIXER", 42, b""),
    ("MSP_RX_CONFIG", 44, b""),
    ("MSP_LED_COLORS", 46, b""),
    ("MSP_LED_STRIP_CONFIG", 48, b""),
    ("MSP_RSSI_CONFIG", 50, b""),
    ("MSP_ADJUSTMENT_RANGES", 52, b""),
    ("MSP_CF_SERIAL_CONFIG", 54, b""),
    ("MSP_VOLTAGE_METER_CONFIG", 56, b""),
    ("MSP_SONAR_ALTITUDE", 58, b""),
    ("MSP_ARMING_CONFIG", 61, b""),
    ("MSP_RX_MAP", 64, b""),
    ("MSP_DATAFLASH_SUMMARY", 70, b""),
    ("MSP_LOOP_TIME", 73, b""),
    ("MSP_FAILSAFE_CONFIG", 75, b""),
    ("MSP_RXFAIL_CONFIG", 77, b""),
    ("MSP_SDCARD_SUMMARY", 79, b""),
    ("MSP_BLACKBOX_CONFIG", 80, b""),
    ("MSP_TRANSPONDER_CONFIG", 82, b""),
    ("MSP_OSD_CONFIG", 84, b""),
    ("MSP_VTX_CONFIG", 88, b""),
    ("MSP_SENSOR_CONFIG", 96, b""),
    ("MSP_STATUS", 101, b""),
    ("MSP_RAW_IMU", 102, b""),
    ("MSP_SERVO", 103, b""),
    ("MSP_MOTOR", 104, b""),
    ("MSP_RC", 105, b""),
    ("MSP_RAW_GPS", 106, b""),
    ("MSP_COMP_GPS", 107, b""),
    ("MSP_ATTITUDE", 108, b""),
    ("MSP_ALTITUDE", 109, b""),
    ("MSP_ANALOG", 110, b""),
    ("MSP_RC_TUNING", 111, b""),
    ("MSP_PID", 112, b""),
    ("MSP_ACTIVEBOXES", 113, b""),
    ("MSP_MISC", 114, b""),
    ("MSP_MOTOR_PINS", 115, b""),
    ("MSP_BOXNAMES", 116, b""),
    ("MSP_PIDNAMES", 117, b""),
    ("MSP_BOXIDS", 119, b""),
    ("MSP_SERVO_CONFIGURATIONS", 120, b""),
    ("MSP_NAV_STATUS", 121, b""),
    ("MSP_3D", 124, b""),
    ("MSP_RC_DEADBAND", 125, b""),
    ("MSP_SENSOR_ALIGNMENT", 126, b""),
    ("MSP_LED_STRIP_MODECOLOR", 127, b""),
    ("MSP_STATUS_EX", 150, b""),
    ("MSP_UID", 160, b""),
    ("MSP2_COMMON_TZ", 0x1001, b""),
    ("MSP2_COMMON_MOTOR_MIXER", 0x1005, b""),
    ("MSP2_COMMON_PG_LIST", 0x1008, b""),
    ("MSP2_COMMON_SERIAL_CONFIG", 0x1009, b""),
    ("MSP2_INAV_STATUS", 0x2000, b""),
    ("MSP2_INAV_OPTICAL_FLOW", 0x2001, b""),
    ("MSP2_INAV_ANALOG", 0x2002, b""),
    ("MSP2_INAV_MISC", 0x2003, b""),
    ("MSP2_INAV_BATTERY_CONFIG", 0x2005, b""),
    ("MSP2_INAV_RATE_PROFILE", 0x2007, b""),
    ("MSP2_INAV_AIR_SPEED", 0x2009, b""),
    ("MSP2_INAV_OUTPUT_MAPPING", 0x200A, b""),
    ("MSP2_INAV_MC_BRAKING", 0x200B, b""),
    ("MSP2_INAV_MIXER", 0x2010, b""),
    ("MSP2_INAV_OSD_LAYOUTS", 0x2012, b""),
    ("MSP2_INAV_MISC2", 0x203A, b""),
    ("MSP2_INAV_RATE_DYNAMICS", 0x2060, b""),
    ("MSP2_INAV_EZ_TUNE", 0x2070, b""),
]

# 실시간으로 여러 번 샘플링할 명령
LIVE_SERIES: list[tuple[str, int]] = [
    ("MSP_ATTITUDE", 108),
    ("MSP_ALTITUDE", 109),
    ("MSP_RAW_IMU", 102),
    ("MSP_RC", 105),
    ("MSP_MOTOR", 104),
    ("MSP_SONAR_ALTITUDE", 58),
    ("MSP2_INAV_OPTICAL_FLOW", 0x2001),
    ("MSP2_INAV_ANALOG", 0x2002),
    ("MSP2_INAV_STATUS", 0x2000),
    ("MSP_ACTIVEBOXES", 113),
]


class UnsafeCommand(RuntimeError):
    pass


def _guard(cmd: int) -> None:
    if cmd in NEVER_SEND:
        raise UnsafeCommand(f"cmd {cmd}는 값을 바꾸는 명령입니다. 스냅샷 수집은 조회만 합니다.")
    if cmd not in {c for _, c, _ in SAFE_GET} | {C.MSP2_COMMON_SETTING_INFO, C.MSP2_COMMON_SETTING}:
        raise UnsafeCommand(f"cmd {cmd}는 허용목록에 없습니다.")


def capture(fc, samples: int = 40, hz: float = 10.0, progress: Callable | None = None) -> dict[str, Any]:
    """FC의 모든 조회 응답을 원본 바이트로 수집한다."""
    snap: dict[str, Any] = {
        "schema": "d4d.fc.snapshot/1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "port": getattr(fc, "port_name", "?"),
        "read_only": True,
        "note": "원본 MSP payload(hex). replay.ReplayClient로 되살릴 수 있다.",
    }

    # ------------------------------------------------ 단발성 조회
    frames: dict[str, Any] = {}
    unsupported: list[str] = []
    for name, cmd, payload in SAFE_GET:
        _guard(cmd)
        try:
            p = fc.request(cmd, payload, retries=1, timeout=0.8)
            frames[name] = {"cmd": cmd, "hex": p.hex()}
        except MspError:
            unsupported.append(name)
    snap["frames"] = frames
    snap["unsupported"] = unsupported
    if progress:
        progress(f"조회 {len(frames)}개 수집, 미지원 {len(unsupported)}개")

    # ------------------------------------------------ 설정 전체 (원본 + 해석)
    _guard(C.MSP2_COMMON_SETTING_INFO)
    pg = frames.get("MSP2_COMMON_PG_LIST")
    max_idx = -1
    groups = []
    if pg:
        raw = bytes.fromhex(pg["hex"])
        for i in range(len(raw) // 6):
            pgn, start, end = struct.unpack_from("<HHH", raw, i * 6)
            groups.append({"pgn": pgn, "start": start, "end": end})
            max_idx = max(max_idx, end)
    settings_raw: dict[str, str] = {}
    items: list[dict] = []
    failed: list[int] = []
    t0 = time.monotonic()
    for idx in range(max_idx + 1):
        try:
            p = fc.request(C.MSP2_COMMON_SETTING_INFO, b"\x00" + struct.pack("<H", idx),
                           retries=1, timeout=0.8)
        except MspError:
            failed.append(idx)
            continue
        info = D.setting_info(p)
        settings_raw[str(idx)] = p.hex()
        items.append(info)
        if progress and idx and idx % 200 == 0:
            progress(f"설정 {idx}/{max_idx}")
    snap["settings"] = {
        "parameter_groups": groups,
        "count": len(items),
        "expected": max_idx + 1,
        "failed_indices": failed,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "raw_by_index": settings_raw,
        "name_to_index": {i["name"]: i["index"] for i in items if "name" in i},
        "items": items,
    }
    if progress:
        progress(f"설정 {len(items)}/{max_idx + 1}개 수집")

    # ------------------------------------------------ 실시간 시계열
    series: dict[str, list[dict]] = {n: [] for n, _ in LIVE_SERIES}
    t0 = time.monotonic()
    for k in range(samples):
        for name, cmd in LIVE_SERIES:
            _guard(cmd)
            try:
                p = fc.request(cmd, b"", retries=0, timeout=0.5)
            except MspError:
                continue
            series[name].append({"t": round(time.monotonic() - t0, 3), "hex": p.hex()})
        time.sleep(max(0.0, 1.0 / hz - 0.01))
    snap["live_series"] = {"hz": hz, "samples": samples, "data": series}
    if progress:
        progress(f"실시간 {samples}회 샘플링 완료 ({time.monotonic() - t0:.1f}초)")
    return snap


def settings_dump_lines(snap: dict) -> list[str]:
    """INAV CLI 형식의 설정 목록. 복구/비교용 기록이다."""
    out = ["# INAV 설정 전체 기록 (d4d_fc snapshot)",
           f"# {snap.get('captured_at')}  {snap.get('settings', {}).get('count')}개",
           "# CLI에 붙여넣기 전에 반드시 내용을 확인하세요.", ""]
    for it in sorted(snap.get("settings", {}).get("items", []), key=lambda x: x.get("name", "")):
        name = it.get("name")
        if "value" not in it:
            out.append(f"# set {name} = ? (원본 {it.get('value_raw')})")
            continue
        v = it["value"]
        if it.get("type") == "string":
            out.append(f"set {name} = {v}" if v else f"# set {name} = (빈 문자열)")
        elif it.get("value_name"):
            out.append(f"set {name} = {it['value_name']}")
        else:
            out.append(f"set {name} = {v}")
    return out

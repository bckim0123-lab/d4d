"""MSP 응답 디코더.

INAV 버전에 따라 payload 길이가 달라질 수 있으므로 모든 디코더는 길이를 검사하고,
해석할 수 없으면 예외 대신 'raw' 항목을 남긴다. 추측으로 값을 만들어내지 않는다.
"""
from __future__ import annotations

import struct
from typing import Any

from . import codes as C


def _u(fmt: str, b: bytes, off: int = 0):
    return struct.unpack_from(fmt, b, off)


def bits(mask: int, table) -> list[str]:
    return [name for bit, name in table if mask & (1 << bit)]


# ---------------------------------------------------------------- 식별


def api_version(p: bytes) -> dict:
    if len(p) < 3:
        return {"raw": p.hex()}
    return {"msp_protocol": p[0], "api": f"{p[1]}.{p[2]}"}


def fc_version(p: bytes) -> dict:
    if len(p) < 3:
        return {"raw": p.hex()}
    return {"version": f"{p[0]}.{p[1]}.{p[2]}"}


def board_info(p: bytes) -> dict:
    if len(p) < 7:
        return {"raw": p.hex()}
    out: dict[str, Any] = {
        "board_id": p[0:4].decode("ascii", "replace").rstrip("\x00"),
        "hw_revision": _u("<H", p, 4)[0],
    }
    # 이후 레이아웃은 버전별로 다르므로, 뒤에 붙는 긴 문자열(보드 이름)만 추출한다
    tail = p[6:]
    for i in range(len(tail)):
        n = tail[i]
        if 0 < n <= len(tail) - i - 1:
            s = tail[i + 1 : i + 1 + n]
            if all(32 <= c < 127 for c in s) and n >= 4:
                out["board_name"] = s.decode("ascii")
                break
    out["raw"] = p.hex()
    return out


def build_info(p: bytes) -> dict:
    if len(p) < 19:
        return {"raw": p.hex()}
    return {
        "date": p[0:11].decode("ascii", "replace").rstrip("\x00"),
        "time": p[11:19].decode("ascii", "replace").rstrip("\x00"),
        "git": p[19:].decode("ascii", "replace").rstrip("\x00"),
    }


def uid(p: bytes) -> dict:
    if len(p) != 12:
        return {"raw": p.hex()}
    a, b, c = _u("<III", p)
    return {"uid": f"{a:08X}{b:08X}{c:08X}"}


# ---------------------------------------------------------------- 상태


def inav_status(p: bytes) -> dict:
    if len(p) < 15:
        return {"raw": p.hex()}
    cycle, i2c_err, sensors, load = _u("<HHHH", p)
    prof = p[8]
    arming = _u("<I", p, 9)[0]
    out = {
        "cycle_time_us": cycle,
        "i2c_errors": i2c_err,
        "sensors_mask": sensors,
        "sensors": bits(sensors, C.SENSOR_BITS),
        "cpu_load_pct": load,
        "pid_profile": (prof & 0x0F) + 1,
        "battery_profile": (prof >> 4) + 1,
        "arming_flags": arming,
        "arming": bits(arming, C.ARMING_FLAGS),
        "armed": bool(arming & (1 << 2)),
        "box_mode_flags_raw": p[13:].hex(),
    }
    out["arm_blockers"] = [
        C.ARMING_FLAG_KR.get(n, n) for n in out["arming"] if n.startswith("DISABLED_")
    ]
    return out


def active_boxes(p: bytes, boxids: list[int]) -> list[str]:
    """MSP_ACTIVEBOXES: BOXIDS 순서대로의 비트마스크."""
    mask = int.from_bytes(p, "little")
    out = []
    for i, pid in enumerate(boxids):
        if mask & (1 << i):
            out.append(C.BOX_NAMES.get(pid, f"id{pid}"))
    return out


def status(p: bytes) -> dict:
    if len(p) < 11:
        return {"raw": p.hex()}
    cycle, i2c_err, sensors = _u("<HHH", p)
    flight_modes = _u("<I", p, 6)[0]
    out = {
        "cycle_time_us": cycle,
        "i2c_errors": i2c_err,
        "sensors": bits(sensors, C.SENSOR_BITS),
        "flight_mode_flags": flight_modes,
        "pid_profile": p[10] + 1,
    }
    if len(p) >= 15:
        out["cpu_load_pct"] = _u("<H", p, 11)[0]
        out["arming_flags_16"] = _u("<H", p, 13)[0]
    return out


def sensor_config(p: bytes) -> dict:
    names = ["acc", "baro", "mag", "pitot", "rangefinder", "opflow"]
    if len(p) < len(names):
        return {"raw": p.hex()}
    return {n: p[i] for i, n in enumerate(names)} | {"raw": p.hex()}


# ---------------------------------------------------------------- 센서/비행 데이터


def raw_imu(p: bytes) -> dict:
    if len(p) < 18:
        return {"raw": p.hex()}
    v = _u("<9h", p)
    return {"acc": list(v[0:3]), "gyro": list(v[3:6]), "mag": list(v[6:9])}


def attitude(p: bytes) -> dict:
    if len(p) < 6:
        return {"raw": p.hex()}
    r, pi, y = _u("<3h", p)
    return {"roll_deg": r / 10, "pitch_deg": pi / 10, "yaw_deg": y}


def altitude(p: bytes) -> dict:
    if len(p) < 6:
        return {"raw": p.hex()}
    out = {"est_alt_m": _u("<i", p)[0] / 100, "vario_cm_s": _u("<h", p, 4)[0]}
    if len(p) >= 10:
        out["baro_alt_m"] = _u("<i", p, 6)[0] / 100
    return out


def sonar_altitude(p: bytes) -> dict:
    if len(p) < 4:
        return {"raw": p.hex()}
    v = _u("<i", p)[0]
    return {"rangefinder_cm": v, "valid": v >= 0}


def analog(p: bytes) -> dict:
    if len(p) < 7:
        return {"raw": p.hex()}
    return {
        "vbat_V": p[0] / 10,
        "mah_drawn": _u("<H", p, 1)[0],
        "rssi": _u("<H", p, 3)[0],
        "amperage_A": _u("<h", p, 5)[0] / 100,
    }


def inav_analog(p: bytes) -> dict:
    if len(p) < 24:
        return {"raw": p.hex()}
    flags = p[0]
    vbat, amps = _u("<Hh", p, 1)
    power, mah, mwh, remain = _u("<iIII", p, 5)
    out = {
        "battery_flags": flags,
        "battery_full_when_plugged": bool(flags & 1),
        "battery_use_capacity_thresholds": bool(flags & 2),
        "battery_state": (flags >> 2) & 0x07,
        "cell_count": (flags >> 5) & 0x07,
        "vbat_V": vbat / 100,
        "amperage_A": amps / 100,
        "power_W": power / 100,
        "mah_drawn": mah,
        "mwh_drawn": mwh,
        "remaining_capacity": remain,
    }
    if len(p) >= 24:
        out["battery_pct"] = p[21]
        out["rssi"] = _u("<H", p, 22)[0]
    out["raw"] = p.hex()
    return out


def rc(p: bytes) -> dict:
    n = len(p) // 2
    if n == 0:
        return {"raw": p.hex()}
    ch = list(_u(f"<{n}H", p))
    return {"channel_count": n, "channels_us": ch}


def raw_gps(p: bytes) -> dict:
    if len(p) < 16:
        return {"raw": p.hex()}
    fix, sats = p[0], p[1]
    lat, lon = _u("<ii", p, 2)
    alt, speed, course = _u("<hHH", p, 10)
    out = {
        "fix_type": fix,
        "satellites": sats,
        "lat_deg": lat / 1e7,
        "lon_deg": lon / 1e7,
        "alt_m": alt,
        "ground_speed_cm_s": speed,
        "ground_course_deg": course / 10,
    }
    if len(p) >= 18:
        out["hdop"] = _u("<H", p, 16)[0] / 100
    return out


def opflow(p: bytes) -> dict:
    if len(p) < 9:
        return {"raw": p.hex()}
    return {"quality": p[0], "flow_rate_raw": list(_u("<2i", p, 1)), "raw": p.hex()}


# ---------------------------------------------------------------- 설정/매핑


def boxnames(p: bytes) -> list[str]:
    return [s for s in p.decode("ascii", "replace").split(";") if s]


def mode_ranges(p: bytes) -> list[dict]:
    out = []
    for i in range(len(p) // 4):
        pid, aux, start, end = p[i * 4 : i * 4 + 4]
        if start == end:
            continue  # 비어 있는 슬롯
        out.append(
            {
                "slot": i,
                "permanent_id": pid,
                "mode": C.BOX_NAMES.get(pid, f"id{pid}"),
                "aux_channel": aux + 1,
                "range_us": [900 + 25 * start, 900 + 25 * end],
            }
        )
    return out


def motor_mixer(p: bytes) -> list[dict]:
    """MSP2_COMMON_MOTOR_MIXER: 모터당 u16 4개. 값 = (계수 + 2.0) * 1000."""
    out = []
    for i in range(len(p) // 8):
        thr, roll, pitch, yaw = _u("<4H", p, i * 8)
        f = [round(v / 1000 - 2.0, 3) for v in (thr, roll, pitch, yaw)]
        if all(abs(x) < 1e-9 for x in f):
            continue  # 사용하지 않는 모터
        out.append({"motor": i + 1, "throttle": f[0], "roll": f[1], "pitch": f[2], "yaw": f[3]})
    return out


FAILSAFE_PROCEDURES = {0: "LAND(자동 착륙)", 1: "DROP(모터 정지)", 2: "RTH(복귀)", 3: "NONE(아무것도 안 함)"}


def failsafe_config(p: bytes) -> dict:
    if len(p) < 8:
        return {"raw": p.hex()}
    out = {
        "delay_ds": p[0],
        "off_delay_ds": p[1],
        "throttle_us": _u("<H", p, 2)[0],
        "kill_switch": p[4],
        "throttle_low_delay": _u("<H", p, 5)[0],
        "procedure_code": p[7],
        "procedure": FAILSAFE_PROCEDURES.get(p[7], f"code{p[7]}"),
    }
    if len(p) >= 9:
        out["recovery_delay_ds"] = p[8]
    out["raw"] = p.hex()
    return out


def feature_mask(p: bytes) -> dict:
    if len(p) < 4:
        return {"raw": p.hex()}
    return {"mask": _u("<I", p)[0]}


def serial_config_v2(p: bytes) -> list[dict]:
    """MSP2_COMMON_SERIAL_CONFIG: 포트당 9바이트."""
    if len(p) % 9 != 0:
        return [{"raw": p.hex()}]
    out = []
    for i in range(len(p) // 9):
        ident = p[i * 9]
        fmask = _u("<I", p, i * 9 + 1)[0]
        msp_b, gps_b, tel_b, per_b = p[i * 9 + 5 : i * 9 + 9]
        out.append(
            {
                "port_id": ident,
                "port_label": _port_label(ident),
                "function_mask": fmask,
                "functions": bits(fmask, C.SERIAL_FUNCTIONS) or ["(없음)"],
                "msp_baud": C.BAUDRATES.get(msp_b, msp_b),
                "gps_baud": C.BAUDRATES.get(gps_b, gps_b),
                "telemetry_baud": C.BAUDRATES.get(tel_b, tel_b),
                "peripheral_baud": C.BAUDRATES.get(per_b, per_b),
            }
        )
    return out


def _port_label(ident: int) -> str:
    if ident <= 9:
        return f"UART{ident + 1}"
    if ident == 20:
        return "USB VCP"
    if 30 <= ident <= 39:
        return f"SOFTSERIAL{ident - 29}"
    return f"ID{ident}"


def output_mapping(p: bytes) -> dict:
    return {"outputs": len(p), "usage_flags": list(p), "raw": p.hex()}


def inav_mixer(p: bytes) -> dict:
    if len(p) < 9:
        return {"raw": p.hex()}
    return {
        "motor_direction_inverted": p[0],
        "raw": p.hex(),
    }


# ---------------------------------------------------------------- 설정 항목


def setting_info(p: bytes) -> dict:
    """MSP2_COMMON_SETTING_INFO 응답.

    name\\0 | pgn u16 | type u8 | section u8 | mode u8 | min i32 | max u32 |
    index u16 | profile_cur u8 | profile_count u8 | [lookup 문자열들] | value
    """
    nul = p.find(b"\x00")
    if nul < 0 or len(p) < nul + 1 + 15:
        return {"raw": p.hex()}
    name = p[:nul].decode("ascii", "replace")
    o = nul + 1
    pgn = _u("<H", p, o)[0]
    type_b, section_b, mode_b = p[o + 2], p[o + 3], p[o + 4]
    vmin = _u("<i", p, o + 5)[0]
    vmax = _u("<I", p, o + 9)[0]
    index = _u("<H", p, o + 13)[0]
    prof_cur, prof_cnt = p[o + 15], p[o + 16]
    o += 17

    tnum = type_b & 0x07
    tname = C.SETTING_TYPES.get(tnum, f"type{tnum}")
    is_lookup = bool(mode_b & 0x40)

    lookup: list[str] = []
    if is_lookup:
        for _ in range(vmin, vmax + 1):
            nxt = p.find(b"\x00", o)
            if nxt < 0:
                break
            lookup.append(p[o:nxt].decode("ascii", "replace"))
            o = nxt + 1

    raw_val = p[o:]
    out: dict[str, Any] = {
        "name": name,
        "index": index,
        "pgn": pgn,
        "type": tname,
        "section": C.SETTING_SECTIONS.get(section_b >> 3, f"section{section_b}"),
        "min": vmin,
        "max": vmax,
        "profile_current": prof_cur,
        "profile_count": prof_cnt,
    }
    if tname == "string":
        out["value"] = raw_val.split(b"\x00")[0].decode("ascii", "replace")
    elif tnum in C.SETTING_FMT and len(raw_val) >= C.SETTING_SIZES[tnum]:
        out["value"] = _u(C.SETTING_FMT[tnum], raw_val)[0]
        if tname == "float":
            out["value"] = round(out["value"], 6)
    else:
        out["value_raw"] = raw_val.hex()
    if lookup:
        out["lookup"] = lookup
        v = out.get("value")
        if isinstance(v, int) and vmin <= v <= vmax and (v - vmin) < len(lookup):
            out["value_name"] = lookup[v - vmin]
    if len(raw_val) != C.SETTING_SIZES.get(tnum, len(raw_val)) and tname != "string":
        out["decode_warning"] = f"값 길이 {len(raw_val)}가 타입 {tname}과 다릅니다"
    return out

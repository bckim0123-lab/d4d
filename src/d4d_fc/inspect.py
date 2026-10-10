"""연결된 비행제어기를 읽기 전용으로 조사한다.

쓰기/무장 관련 명령은 전혀 보내지 않는다. 보내는 것은 모두 조회(request)뿐이다.
"""
from __future__ import annotations

import struct
import time
from typing import Any

from . import codes as C
from . import decode as D
from .msp import MspClient, MspError

# 이 프로젝트(EMP/재밍 자율비행)와 직접 관련된 설정 이름들
RELEVANT_PREFIXES = (
    "nav_", "inav_", "opflow_", "rangefinder_", "align_", "failsafe_", "mc_",
    "serialrx_", "receiver_", "msp_", "telemetry_", "gps_", "mag_", "acc_",
    "baro_", "imu_", "ahrs_", "applied_", "platform_", "model_",
)


def gather(fc: MspClient, with_settings: bool = True, settings_progress=None) -> dict[str, Any]:
    out: dict[str, Any] = {"port": fc.port_name, "read_only": True}

    # ------------------------------------------------ 식별
    ident: dict[str, Any] = {}
    v = fc.try_request(C.MSP_FC_VARIANT)
    ident["variant"] = v.decode("ascii", "replace") if v else None
    for key, cmd, dec in (
        ("api", C.MSP_API_VERSION, D.api_version),
        ("firmware", C.MSP_FC_VERSION, D.fc_version),
        ("board", C.MSP_BOARD_INFO, D.board_info),
        ("build", C.MSP_BUILD_INFO, D.build_info),
        ("uid", C.MSP_UID, D.uid),
    ):
        p = fc.try_request(cmd)
        if p is not None:
            ident[key] = dec(p)
    p = fc.try_request(C.MSP_NAME)
    if p is not None:
        ident["craft_name"] = p.decode("ascii", "replace").rstrip("\x00") or "(미설정)"
    out["identity"] = ident

    # ------------------------------------------------ 상태 / 센서
    st: dict[str, Any] = {}
    p = fc.try_request(C.MSP2_INAV_STATUS)
    if p is not None:
        st["inav_status"] = D.inav_status(p)
    p = fc.try_request(C.MSP_STATUS)
    if p is not None:
        st["status"] = D.status(p)
    p = fc.try_request(C.MSP_SENSOR_CONFIG)
    if p is not None:
        st["sensor_config"] = D.sensor_config(p)
    out["status"] = st

    # ------------------------------------------------ 실시간 데이터
    live: dict[str, Any] = {}
    for key, cmd, dec in (
        ("attitude", C.MSP_ATTITUDE, D.attitude),
        ("altitude", C.MSP_ALTITUDE, D.altitude),
        ("raw_imu", C.MSP_RAW_IMU, D.raw_imu),
        ("rangefinder", C.MSP_SONAR_ALTITUDE, D.sonar_altitude),
        ("opflow", C.MSP2_INAV_OPTICAL_FLOW, D.opflow),
        ("gps", C.MSP_RAW_GPS, D.raw_gps),
        ("analog", C.MSP_ANALOG, D.analog),
        ("inav_analog", C.MSP2_INAV_ANALOG, D.inav_analog),
        ("rc", C.MSP_RC, D.rc),
    ):
        p = fc.try_request(cmd)
        if p is not None:
            live[key] = dec(p)
    p = fc.try_request(C.MSP_MOTOR)
    if p is not None and len(p) >= 2:
        n = len(p) // 2
        live["motor_outputs_us"] = list(struct.unpack_from(f"<{n}H", p))
    out["live"] = live

    # ------------------------------------------------ 모드 / 비행 모드 스위치
    modes: dict[str, Any] = {}
    p = fc.try_request(C.MSP_BOXIDS)
    ids = list(p) if p is not None else []
    if ids:
        modes["available_mode_ids"] = ids
        modes["available_modes"] = [C.BOX_NAMES.get(i, f"id{i}") for i in ids]
    p = fc.try_request(C.MSP_MODE_RANGES)
    if p is not None:
        modes["configured_mode_switches"] = D.mode_ranges(p)
    p = fc.try_request(C.MSP_ACTIVEBOXES)
    if p is not None and ids:
        modes["active_modes"] = D.active_boxes(p, ids)
    out["modes"] = modes

    # ------------------------------------------------ 포트 / 출력
    hw: dict[str, Any] = {}
    p = fc.try_request(C.MSP2_COMMON_SERIAL_CONFIG)
    if p is not None:
        hw["serial_ports"] = D.serial_config_v2(p)
    p = fc.try_request(C.MSP2_INAV_OUTPUT_MAPPING)
    if p is not None:
        hw["output_mapping"] = D.output_mapping(p)
    p = fc.try_request(C.MSP_FEATURE)
    if p is not None:
        hw["feature_mask"] = D.feature_mask(p)
    p = fc.try_request(C.MSP2_COMMON_MOTOR_MIXER)
    if p is not None:
        hw["motor_mixer"] = D.motor_mixer(p)
        hw["motor_count"] = len(hw["motor_mixer"])
    p = fc.try_request(C.MSP_FAILSAFE_CONFIG)
    if p is not None:
        hw["failsafe"] = D.failsafe_config(p)
    out["hardware"] = hw

    # ------------------------------------------------ 전체 설정 목록
    if with_settings:
        out["settings"] = read_all_settings(fc, settings_progress)

    out["unsupported_commands"] = sorted(set(fc.last_rejected))
    return out


def read_all_settings(fc: MspClient, progress=None) -> dict[str, Any]:
    """PG 목록으로 설정 인덱스 범위를 구한 뒤 전부 읽는다."""
    pg = fc.try_request(C.MSP2_COMMON_PG_LIST)
    if pg is None or len(pg) % 6 != 0:
        return {"error": "PG_LIST를 읽을 수 없습니다", "raw": pg.hex() if pg else None}
    groups = []
    max_idx = 0
    for i in range(len(pg) // 6):
        pgn, start, end = struct.unpack_from("<HHH", pg, i * 6)
        groups.append({"pgn": pgn, "start": start, "end": end})
        max_idx = max(max_idx, end)

    items: list[dict] = []
    failed: list[int] = []
    t0 = time.monotonic()
    for idx in range(max_idx + 1):
        try:
            p = fc.request(C.MSP2_COMMON_SETTING_INFO, b"\x00" + struct.pack("<H", idx), retries=1, timeout=0.8)
        except MspError:
            failed.append(idx)
            continue
        if not p:
            failed.append(idx)
            continue
        items.append(D.setting_info(p))
        if progress and idx % 50 == 0:
            progress(idx, max_idx, time.monotonic() - t0)
    return {
        "parameter_groups": groups,
        "count": len(items),
        "expected": max_idx + 1,
        "failed_indices": failed,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "items": items,
    }


def relevant_settings(settings: dict) -> list[dict]:
    out = []
    for s in settings.get("items", []):
        n = s.get("name", "")
        if n.startswith(RELEVANT_PREFIXES):
            out.append(s)
    return out

"""Single-setting, RAM-only writer for the SPIDER ground-control test."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import codes as C
from . import decode as D
from .control import (
    ALLOWED_OVERRIDE_MASKS,
    GROUND_CONTROL_MASKS,
    ControlReadiness,
    evaluate_ground_control,
)
from .msp import MspError


SETTING_NAME = "msp_override_channels"
CONFIRMATION = "프로펠러 제거 / 디스아밍"
ALLOWED_VALUES = ALLOWED_OVERRIDE_MASKS
ALLOWED_WRITE_COMMANDS = {C.MSP2_COMMON_SET_SETTING}


class ControlWriteBlocked(RuntimeError):
    """The requested mutation failed a safety or verification condition."""


def _guard_write_command(command: int) -> None:
    if command not in ALLOWED_WRITE_COMMANDS:
        raise ControlWriteBlocked(f"허용되지 않은 쓰기 명령입니다: {command}")


def _required(fc: Any, command: int, payload: bytes = b"") -> bytes:
    try:
        response = fc.request(command, payload)
    except MspError as exc:
        raise ControlWriteBlocked(
            f"FC 조회 실패(cmd={command}, 0x{command:X}): {exc}"
        ) from exc
    if response is None:
        raise ControlWriteBlocked(
            f"FC 조회 응답이 없습니다(cmd={command}, 0x{command:X})."
        )
    return response


def _setting_info(fc: Any, name: str) -> dict[str, Any]:
    raw = _required(fc, C.MSP2_COMMON_SETTING_INFO, name.encode("ascii") + b"\x00")
    decoded = D.setting_info(raw)
    if decoded.get("name") != name or "value" not in decoded:
        raise ControlWriteBlocked(f"{name} 설정값을 확정적으로 읽지 못했습니다.")
    return decoded


def read_control_state(fc: Any) -> tuple[dict[str, Any], ControlReadiness]:
    """Read every safety input afresh in one short FC session."""

    variant_raw = _required(fc, C.MSP_FC_VARIANT)
    version_raw = _required(fc, C.MSP_FC_VERSION)
    board_raw = _required(fc, C.MSP_BOARD_INFO)
    status_raw = _required(fc, C.MSP2_INAV_STATUS)
    rc_raw = _required(fc, C.MSP_RC)
    motor_raw = _required(fc, C.MSP_MOTOR)
    modes_raw = _required(fc, C.MSP_MODE_RANGES)
    serial_raw = _required(fc, C.MSP2_COMMON_SERIAL_CONFIG)
    min_check = _setting_info(fc, "min_check")
    override = _setting_info(fc, SETTING_NAME)

    motor_count = len(motor_raw) // 2
    motors = (
        list(struct.unpack(f"<{motor_count}H", motor_raw))
        if motor_count
        else []
    )
    data: dict[str, Any] = {
        "identity": {
            "variant": variant_raw.decode("ascii", "replace"),
            "firmware": D.fc_version(version_raw),
            "board": D.board_info(board_raw),
        },
        "status": {"inav_status": D.inav_status(status_raw)},
        "live": {
            "rc": D.rc(rc_raw),
            "motor_outputs_us": motors,
        },
        "modes": {
            "configured_mode_switches": D.mode_ranges(modes_raw),
        },
        "hardware": {
            "serial_ports": D.serial_config_v2(serial_raw),
        },
        "settings": {
            "items": [min_check, override],
        },
    }
    readiness = evaluate_ground_control(
        data,
        current_mask=int(override["value"]),
        min_check=int(min_check["value"]),
    )
    return data, readiness


def _write_backup(
    previous_value: int,
    target_value: int,
    readiness: ControlReadiness,
    backup_dir: str | Path,
) -> Path:
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(timezone.utc)
    path = directory / (
        "msp_override_channels_ram_"
        + captured_at.strftime("%Y%m%dT%H%M%S_%fZ")
        + ".json"
    )
    document = {
        "captured_at": captured_at.isoformat(),
        "setting": SETTING_NAME,
        "previous_value": previous_value,
        "requested_value": target_value,
        "storage": "RAM only; not persisted",
        "cli_restore": f"set {SETTING_NAME} = {previous_value}",
        "readiness": readiness.to_dict(),
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_ram_setting(
    fc: Any,
    setting_name: str,
    value: int,
    *,
    confirmation: str | None,
    writes_enabled: bool,
    backup_dir: str | Path = "snapshots",
) -> dict[str, Any]:
    """Apply an allowlisted mask in volatile memory and verify by re-reading."""

    if setting_name != SETTING_NAME:
        raise ControlWriteBlocked(
            f"허용된 설정은 {SETTING_NAME} 하나뿐입니다."
        )
    if type(value) is not int or value not in ALLOWED_VALUES:
        raise ControlWriteBlocked("허용된 값은 7/67/71(수평 제어 시험)과 64(복구)뿐입니다.")
    if not writes_enabled:
        raise ControlWriteBlocked("하드웨어 쓰기 기능이 비활성화되어 있습니다.")
    if value in GROUND_CONTROL_MASKS and confirmation != CONFIRMATION:
        raise ControlWriteBlocked(f"확인 문구를 정확히 입력하세요: {CONFIRMATION}")

    _data, readiness = read_control_state(fc)
    permitted = (
        readiness.can_apply_override
        if value in GROUND_CONTROL_MASKS
        else readiness.can_restore_64
    )
    if not permitted:
        failed = [
            gate.label
            for gate in (
                readiness.apply_gates
                if value in GROUND_CONTROL_MASKS
                else readiness.restore_gates
            )
            if not gate.passed
        ]
        raise ControlWriteBlocked(
            "안전 게이트가 닫혀 있습니다: " + ", ".join(failed)
        )

    before = readiness.current_mask
    if before is None:
        raise ControlWriteBlocked("현재 Override 마스크를 읽지 못했습니다.")
    backup_path = _write_backup(before, value, readiness, backup_dir)

    if before != value:
        command = C.MSP2_COMMON_SET_SETTING
        _guard_write_command(command)
        payload = SETTING_NAME.encode("ascii") + b"\x00" + struct.pack("<I", value)
        try:
            fc.request(command, payload)
        except MspError as exc:
            raise ControlWriteBlocked(f"RAM 설정 쓰기가 거부됐습니다: {exc}") from exc

    after_info = _setting_info(fc, SETTING_NAME)
    after = int(after_info["value"])
    if after != value:
        raise ControlWriteBlocked(
            f"되읽기 검증 실패: 요청 {value}, 실제 {after}. "
            "영구 저장은 수행하지 않았습니다."
        )

    return {
        "setting": SETTING_NAME,
        "before": before,
        "requested": value,
        "after": after,
        "verified": True,
        "saved": False,
        "backup": str(backup_path),
        "readiness": readiness.to_dict(),
    }


def apply_override_mask(
    fc: Any,
    value: int,
    *,
    confirmation: str | None,
    writes_enabled: bool,
    backup_dir: str | Path = "snapshots",
) -> dict[str, Any]:
    return write_ram_setting(
        fc,
        SETTING_NAME,
        value,
        confirmation=confirmation,
        writes_enabled=writes_enabled,
        backup_dir=backup_dir,
    )

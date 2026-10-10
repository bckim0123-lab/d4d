"""Fail-closed safety evaluation for the SPIDER ground-control path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


EXPECTED_VARIANT = "INAV"
EXPECTED_FIRMWARE = "9.1.0"
EXPECTED_BOARD_NAMES = frozenset({"MICOAIR743V2"})
RESTORE_MASK = 64
GROUND_CONTROL_MASK = 67
# INAV logical bits: roll=1, pitch=2, yaw=4, throttle=8, CH7 kill=64.
# Keep an explicit allowlist; other masks are never inferred to be safe.
GROUND_CONTROL_MASKS = frozenset({7, GROUND_CONTROL_MASK, 71})
ALLOWED_OVERRIDE_MASKS = GROUND_CONTROL_MASKS | {RESTORE_MASK}


@dataclass(frozen=True)
class Gate:
    """One independently explainable condition in the control interlock."""

    key: str
    label: str
    passed: bool
    detail: str
    remediation: str
    required_for_apply: bool = True
    required_for_restore: bool = False


@dataclass(frozen=True)
class ControlReadiness:
    """Result of evaluating a read-only FC snapshot."""

    current_mask: int | None
    min_check: int | None
    throttle_us: int | None
    gates: tuple[Gate, ...]

    @property
    def apply_gates(self) -> tuple[Gate, ...]:
        return tuple(gate for gate in self.gates if gate.required_for_apply)

    @property
    def restore_gates(self) -> tuple[Gate, ...]:
        return tuple(gate for gate in self.gates if gate.required_for_restore)

    @property
    def can_apply_override(self) -> bool:
        return bool(self.apply_gates) and all(gate.passed for gate in self.apply_gates)

    @property
    def can_apply_67(self) -> bool:
        """Compatibility alias for clients using the original mask-67 gate."""
        return self.can_apply_override

    @property
    def can_restore_64(self) -> bool:
        return bool(self.restore_gates) and all(gate.passed for gate in self.restore_gates)

    def by_key(self, key: str) -> Gate:
        for gate in self.gates:
            if gate.key == key:
                return gate
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_mask": self.current_mask,
            "target_mask": GROUND_CONTROL_MASK,
            "target_masks": sorted(GROUND_CONTROL_MASKS),
            "allowed_masks": sorted(ALLOWED_OVERRIDE_MASKS),
            "restore_mask": RESTORE_MASK,
            "min_check": self.min_check,
            "throttle_us": self.throttle_us,
            "can_apply_67": self.can_apply_67,
            "can_apply_override": self.can_apply_override,
            "can_restore_64": self.can_restore_64,
            "gates": [asdict(gate) for gate in self.gates],
        }


def _setting_value(fc_data: dict[str, Any], name: str) -> Any:
    items = fc_data.get("settings", {}).get("items", [])
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("value")
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_serial_function(
    ports: Iterable[dict[str, Any]], port_label: str, function: str
) -> bool:
    return any(
        port.get("port_label") == port_label
        and function in (port.get("functions") or [])
        for port in ports
        if isinstance(port, dict)
    )


def evaluate_ground_control(
    fc_data: dict[str, Any],
    current_mask: int | None = None,
    min_check: int | None = None,
) -> ControlReadiness:
    """Evaluate this snapshot for RAM-only masks 7/67/71 or restoration to 64.

    Missing or malformed evidence always closes the relevant gate. Restoring
    mask 64 deliberately needs fewer conditions than granting roll/pitch
    control, but it still requires the expected FC and a positively confirmed
    disarmed state.
    """

    identity = fc_data.get("identity", {})
    variant = identity.get("variant")
    firmware = identity.get("firmware", {}).get("version")
    board_name = identity.get("board", {}).get("board_name")
    identity_ok = (
        variant == EXPECTED_VARIANT
        and firmware == EXPECTED_FIRMWARE
        and board_name in EXPECTED_BOARD_NAMES
    )

    inav_status = fc_data.get("status", {}).get("inav_status", {})
    armed = inav_status.get("armed")
    disarmed = armed is False

    rc = fc_data.get("live", {}).get("rc", {})
    channels = rc.get("channels_us", [])
    throttle_us = (
        _as_int(channels[3])
        if isinstance(channels, list) and len(channels) >= 4
        else None
    )

    resolved_min_check = _as_int(
        min_check if min_check is not None else _setting_value(fc_data, "min_check")
    )
    throttle_low = (
        throttle_us is not None
        and resolved_min_check is not None
        and throttle_us < resolved_min_check
    )

    motor_outputs = fc_data.get("live", {}).get("motor_outputs_us", [])
    motors_idle = (
        isinstance(motor_outputs, list)
        and len(motor_outputs) >= 4
        and all(_as_int(value) == 1000 for value in motor_outputs[:4])
    )

    arming = inav_status.get("arming")
    receiver_link = (
        isinstance(arming, list)
        and "DISABLED_RC_LINK" not in arming
        and _as_int(rc.get("channel_count")) is not None
        and int(rc["channel_count"]) >= 4
    )

    ports = fc_data.get("hardware", {}).get("serial_ports", [])
    if not isinstance(ports, list):
        ports = []
    uart5_msp = _has_serial_function(ports, "UART5", "MSP")
    uart6_receiver = _has_serial_function(ports, "UART6", "RX_SERIAL")

    mode_switches = fc_data.get("modes", {}).get("configured_mode_switches", [])
    override_mode = isinstance(mode_switches, list) and any(
        isinstance(mode, dict) and _as_int(mode.get("permanent_id")) == 50
        for mode in mode_switches
    )

    resolved_mask = _as_int(
        current_mask
        if current_mask is not None
        else _setting_value(fc_data, "msp_override_channels")
    )
    mask_known_safe = resolved_mask in ALLOWED_OVERRIDE_MASKS

    gates = (
        Gate(
            "identity",
            "FC 식별",
            identity_ok,
            f"{variant or '?'} {firmware or '?'} / {board_name or '?'}",
            "MicoAir H743 V2와 INAV 9.1.0 연결을 확인하세요.",
            required_for_restore=True,
        ),
        Gate(
            "disarmed",
            "비무장",
            disarmed,
            "DISARMED" if disarmed else ("ARMED" if armed is True else "확인 불가"),
            "FC를 비무장 상태로 만들고 다시 읽으세요.",
            required_for_restore=True,
        ),
        Gate(
            "override_mask",
            "Override 마스크",
            mask_known_safe,
            str(resolved_mask) if resolved_mask is not None else "확인 불가",
            "마스크를 임의 변경하지 말고 허용된 7/64/67/71 상태를 확인하세요.",
            required_for_restore=True,
        ),
        Gate(
            "throttle_low",
            "저스로틀",
            throttle_low,
            f"{throttle_us if throttle_us is not None else '?'} < "
            f"{resolved_min_check if resolved_min_check is not None else '?'} µs",
            "송신기 스로틀을 min_check 아래로 내리세요.",
        ),
        Gate(
            "motors_idle",
            "모터 정지",
            motors_idle,
            str(motor_outputs[:4]) if isinstance(motor_outputs, list) else "확인 불가",
            "모터 테스트를 중지하고 M1~M4가 모두 1000 µs인지 확인하세요.",
        ),
        Gate(
            "receiver_link",
            "수동 회수 링크",
            receiver_link,
            (
                "정상"
                if receiver_link
                else "RC 링크 차단 또는 채널 텔레메트리 확인 불가"
            ),
            "UART6 iBUS 수신기와 송신기 링크를 복구하세요.",
        ),
        Gate(
            "uart6_receiver",
            "UART6 수신기",
            uart6_receiver,
            "RX_SERIAL" if uart6_receiver else "RX_SERIAL 미확인",
            "UART6 RX_SERIAL 설정을 확인하세요.",
        ),
        Gate(
            "uart5_msp",
            "ESP32 제어 경로",
            uart5_msp,
            "UART5 MSP" if uart5_msp else "UART5 MSP 미확인",
            "FC UART5의 MSP 기능을 확인하세요.",
        ),
        Gate(
            "override_mode",
            "MSP RC OVERRIDE 모드",
            override_mode,
            "Permanent ID 50" if override_mode else "모드 범위 미확인",
            "AUX3에 MSP RC OVERRIDE 범위가 설정됐는지 확인하세요.",
        ),
    )

    return ControlReadiness(
        current_mask=resolved_mask,
        min_check=resolved_min_check,
        throttle_us=throttle_us,
        gates=gates,
    )

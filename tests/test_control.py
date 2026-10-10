import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def safe_fc_data() -> dict:
    return {
        "identity": {
            "variant": "INAV",
            "firmware": {"version": "9.1.0"},
            "board": {"board_name": "MICOAIR743V2", "board_id": "M7V2"},
        },
        "status": {
            "inav_status": {
                "armed": False,
                "arming": [],
                "arm_blockers": [],
                "sensors": [
                    "가속도계",
                    "기압계",
                    "지자기",
                    "레인지파인더",
                    "광류(OPFLOW)",
                ],
            }
        },
        "live": {
            "rc": {
                "channel_count": 18,
                # MSP_RC: roll, pitch, yaw, throttle, AUX1...
                "channels_us": [1500, 1500, 1500, 1000, 1000, 1000, 1000, 1500],
            },
            "motor_outputs_us": [1000, 1000, 1000, 1000],
        },
        "modes": {
            "configured_mode_switches": [
                {
                    "permanent_id": 50,
                    "mode": "MSP RC OVERRIDE",
                    "aux_channel": 2,
                    "range_us": [900, 1700],
                }
            ],
        },
        "hardware": {
            "serial_ports": [
                {"port_label": "USB VCP", "functions": ["MSP"]},
                {"port_label": "UART4", "functions": ["MSP"]},
                {"port_label": "UART5", "functions": ["MSP"]},
                {"port_label": "UART6", "functions": ["RX_SERIAL"]},
            ],
        },
        "settings": {
            "items": [
                {"name": "msp_override_channels", "value": 64},
                {"name": "min_check", "value": 1100},
            ]
        },
    }


class GroundControlReadinessTests(unittest.TestCase):
    def evaluate(self, data=None):
        from d4d_fc.control import evaluate_ground_control

        return evaluate_ground_control(data or safe_fc_data())

    def test_known_disarmed_idle_fc_can_apply_roll_pitch_override(self):
        result = self.evaluate()
        self.assertTrue(result.can_apply_67)
        self.assertTrue(result.can_restore_64)
        self.assertTrue(all(g.passed for g in result.apply_gates))

    def test_armed_fc_is_blocked(self):
        data = safe_fc_data()
        data["status"]["inav_status"]["armed"] = True
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.can_restore_64)
        self.assertFalse(result.by_key("disarmed").passed)

    def test_throttle_at_min_check_is_blocked(self):
        data = safe_fc_data()
        data["live"]["rc"]["channels_us"][3] = 1100
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.by_key("throttle_low").passed)

    def test_non_idle_motor_is_blocked(self):
        data = safe_fc_data()
        data["live"]["motor_outputs_us"][2] = 1001
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.by_key("motors_idle").passed)

    def test_missing_receiver_link_is_blocked(self):
        data = safe_fc_data()
        data["status"]["inav_status"]["arming"] = ["DISABLED_RC_LINK"]
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.by_key("receiver_link").passed)

    def test_unknown_board_or_firmware_is_blocked(self):
        data = safe_fc_data()
        data["identity"]["board"]["board_name"] = "UNKNOWN"
        data["identity"]["firmware"]["version"] = "8.0.0"
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.by_key("identity").passed)

    def test_unexpected_override_mask_is_blocked(self):
        data = safe_fc_data()
        data["settings"]["items"][0]["value"] = 255
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.can_restore_64)
        self.assertFalse(result.by_key("override_mask").passed)

    def test_only_explicit_ground_control_masks_are_known_safe(self):
        for mask in range(256):
            with self.subTest(mask=mask):
                data = safe_fc_data()
                data["settings"]["items"][0]["value"] = mask
                result = self.evaluate(data)
                allowed = mask in {7, 64, 67, 71}
                self.assertEqual(result.by_key("override_mask").passed, allowed)
                self.assertEqual(result.can_apply_override, allowed)
                self.assertEqual(result.can_apply_67, allowed)
                self.assertEqual(result.can_restore_64, allowed)

    def test_readiness_reports_shared_apply_gate_and_explicit_masks(self):
        result = self.evaluate().to_dict()
        self.assertTrue(result["can_apply_override"])
        self.assertEqual(result["can_apply_override"], result["can_apply_67"])
        self.assertEqual(result["target_masks"], [7, 67, 71])
        self.assertEqual(result["allowed_masks"], [7, 64, 67, 71])

    def test_restore_remains_available_when_only_throttle_is_high(self):
        data = safe_fc_data()
        data["settings"]["items"][0]["value"] = 67
        data["live"]["rc"]["channels_us"][3] = 1960
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertTrue(result.can_restore_64)

    def test_missing_uart5_or_override_mode_is_blocked(self):
        data = safe_fc_data()
        data["hardware"]["serial_ports"][2]["functions"] = ["(없음)"]
        data["modes"]["configured_mode_switches"] = []
        result = self.evaluate(data)
        self.assertFalse(result.can_apply_67)
        self.assertFalse(result.by_key("uart5_msp").passed)
        self.assertFalse(result.by_key("override_mode").passed)


def _setting_info(name: str, value: int) -> bytes:
    if name == "msp_override_channels":
        type_id, minimum, maximum, index, encoded = (
            4,
            0,
            0xFFFFFFFF,
            86,
            struct.pack("<I", value),
        )
    elif name == "min_check":
        type_id, minimum, maximum, index, encoded = (
            2,
            1000,
            2000,
            69,
            struct.pack("<H", value),
        )
    else:
        raise KeyError(name)
    return (
        name.encode("ascii")
        + b"\x00"
        + struct.pack(
            "<HBBB i I H BB",
            24,
            type_id,
            0,
            0,
            minimum,
            maximum,
            index,
            0,
            0,
        )
        + encoded
    )


class _ControlFakeFc:
    def __init__(
        self,
        *,
        mask=64,
        armed=False,
        throttle=1000,
        motors=(1000, 1000, 1000, 1000),
        accept=True,
        verify=True,
    ):
        self.mask = mask
        self.armed = armed
        self.throttle = throttle
        self.motors = list(motors)
        self.accept = accept
        self.verify = verify
        self.sent = []

    def request(self, cmd, payload=b"", **_kwargs):
        from d4d_fc import codes as C
        from d4d_fc.msp import MspError

        self.sent.append((cmd, payload))
        if cmd == C.MSP_FC_VARIANT:
            return b"INAV"
        if cmd == C.MSP_FC_VERSION:
            return bytes([9, 1, 0])
        if cmd == C.MSP_BOARD_INFO:
            return b"M7V2" + struct.pack("<H", 1) + bytes([12]) + b"MICOAIR743V2"
        if cmd == C.MSP2_INAV_STATUS:
            flags = (1 << 2) if self.armed else 0
            return struct.pack("<HHHHBI", 500, 0, 0x7F, 2, 0, flags) + b"\x00\x00"
        if cmd == C.MSP_RC:
            channels = [1500, 1500, 1500, self.throttle, 1000, 1000, 1000, 1500]
            return struct.pack("<8H", *channels)
        if cmd == C.MSP_MOTOR:
            return struct.pack("<8H", *self.motors, 0, 0, 0, 0)
        if cmd == C.MSP_MODE_RANGES:
            return bytes([50, 1, 0, 48])
        if cmd == C.MSP2_COMMON_SERIAL_CONFIG:
            # UART5=MSP, UART6=RX_SERIAL
            return (
                struct.pack("<BI4B", 4, 1, 8, 8, 0, 8)
                + struct.pack("<BI4B", 5, 1 << 6, 8, 8, 0, 8)
            )
        if cmd == C.MSP2_COMMON_SETTING_INFO:
            nul = payload.find(b"\x00")
            name = payload[:nul].decode("ascii")
            value = self.mask if name == "msp_override_channels" else 1100
            return _setting_info(name, value)
        if cmd == C.MSP2_COMMON_SET_SETTING:
            if not self.accept:
                raise MspError("거부")
            prefix = b"msp_override_channels\x00"
            if not payload.startswith(prefix) or len(payload) != len(prefix) + 4:
                raise MspError("잘못된 payload")
            requested = struct.unpack("<I", payload[len(prefix) :])[0]
            if self.verify:
                self.mask = requested
            return b""
        raise MspError(f"지원하지 않는 명령: {cmd}")


class ControlWriteTests(unittest.TestCase):
    confirmation = "프로펠러 제거 / 디스아밍"

    def apply(self, fc, target=67, **kwargs):
        from d4d_fc.control_write import write_ram_setting

        with tempfile.TemporaryDirectory() as td:
            return write_ram_setting(
                fc,
                "msp_override_channels",
                target,
                confirmation=kwargs.pop("confirmation", self.confirmation),
                writes_enabled=kwargs.pop("writes_enabled", True),
                backup_dir=td,
                **kwargs,
            )

    def test_only_allowlisted_setting_and_values_are_accepted(self):
        from d4d_fc.control_write import ControlWriteBlocked, write_ram_setting

        fc = _ControlFakeFc()
        with self.assertRaises(ControlWriteBlocked):
            write_ram_setting(
                fc,
                "min_check",
                1000,
                confirmation=self.confirmation,
                writes_enabled=True,
            )
        with self.assertRaises(ControlWriteBlocked):
            write_ram_setting(
                fc,
                "msp_override_channels",
                255,
                confirmation=self.confirmation,
                writes_enabled=True,
            )
        self.assertEqual(fc.sent, [])

    def test_applying_67_requires_exact_confirmation_and_write_flag(self):
        from d4d_fc import codes as C
        from d4d_fc.control_write import ControlWriteBlocked

        for confirmation, enabled in (
            (self.confirmation, False),
            ("프로펠러 장착 / 디스아밍", True),
        ):
            with self.subTest(confirmation=confirmation, enabled=enabled):
                fc = _ControlFakeFc()
                with self.assertRaises(ControlWriteBlocked):
                    self.apply(
                        fc,
                        confirmation=confirmation,
                        writes_enabled=enabled,
                    )
                self.assertNotIn(
                    C.MSP2_COMMON_SET_SETTING,
                    [cmd for cmd, _payload in fc.sent],
                )

    def test_apply_67_uses_exact_payload_and_verifies_without_eeprom(self):
        from d4d_fc import codes as C

        fc = _ControlFakeFc()
        result = self.apply(fc)
        writes = [
            payload
            for cmd, payload in fc.sent
            if cmd == C.MSP2_COMMON_SET_SETTING
        ]
        self.assertEqual(
            writes,
            [b"msp_override_channels\x00" + struct.pack("<I", 67)],
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["before"], 64)
        self.assertEqual(result["after"], 67)
        self.assertFalse(result["saved"])
        self.assertNotIn(C.MSP_EEPROM_WRITE, [cmd for cmd, _ in fc.sent])

    def test_apply_7_and_71_verify_exact_ram_payload_without_eeprom(self):
        from d4d_fc import codes as C

        for target in (7, 71):
            with self.subTest(target=target):
                fc = _ControlFakeFc()
                result = self.apply(fc, target=target)
                writes = [
                    payload for cmd, payload in fc.sent
                    if cmd == C.MSP2_COMMON_SET_SETTING
                ]
                self.assertEqual(
                    writes, [b"msp_override_channels\x00" + struct.pack("<I", target)]
                )
                self.assertTrue(result["verified"])
                self.assertEqual(result["after"], target)
                self.assertFalse(result["saved"])
                self.assertNotIn(C.MSP_EEPROM_WRITE, [cmd for cmd, _ in fc.sent])

    def test_new_masks_require_exact_confirmation_and_write_flag(self):
        from d4d_fc.control_write import ControlWriteBlocked

        for target in (7, 71):
            for confirmation, enabled in ((None, True), ("디스아밍", True), (self.confirmation, False)):
                with self.subTest(target=target, confirmation=confirmation, enabled=enabled):
                    fc = _ControlFakeFc()
                    with self.assertRaises(ControlWriteBlocked):
                        self.apply(fc, target=target, confirmation=confirmation, writes_enabled=enabled)
                    self.assertEqual(fc.sent, [])

    def test_new_masks_use_all_apply_gates_instead_of_restore_gates(self):
        from d4d_fc import codes as C
        from d4d_fc.control_write import ControlWriteBlocked

        for target in (7, 71):
            for state in ({"armed": True}, {"throttle": 1100}, {"motors": (1000, 1001, 1000, 1000)}):
                with self.subTest(target=target, state=state):
                    fc = _ControlFakeFc(**state)
                    with self.assertRaises(ControlWriteBlocked):
                        self.apply(fc, target=target)
                    self.assertNotIn(C.MSP2_COMMON_SET_SETTING, [cmd for cmd, _ in fc.sent])

    def test_writer_allowlist_rejects_throttle_and_other_unapproved_bits(self):
        from d4d_fc.control_write import ALLOWED_VALUES, ControlWriteBlocked

        self.assertEqual(ALLOWED_VALUES, {7, 64, 67, 71})
        for target in (0, 3, 8, 15, 72, 79, 127, 255, True, 7.0, "7"):
            with self.subTest(target=target):
                fc = _ControlFakeFc()
                with self.assertRaises(ControlWriteBlocked):
                    self.apply(fc, target=target)
                self.assertEqual(fc.sent, [])

    def test_restore_64_from_new_masks_preserves_reduced_restore_gates(self):
        for mask in (7, 71):
            with self.subTest(mask=mask):
                fc = _ControlFakeFc(mask=mask, throttle=1960)
                result = self.apply(fc, target=64, confirmation=None)
                self.assertTrue(result["verified"])
                self.assertEqual(result["after"], 64)

    def test_unsafe_live_states_block_67_before_write(self):
        from d4d_fc import codes as C
        from d4d_fc.control_write import ControlWriteBlocked

        unsafe = (
            {"armed": True},
            {"throttle": 1100},
            {"motors": (1000, 1001, 1000, 1000)},
        )
        for state in unsafe:
            with self.subTest(state=state):
                fc = _ControlFakeFc(**state)
                with self.assertRaises(ControlWriteBlocked):
                    self.apply(fc)
                self.assertNotIn(
                    C.MSP2_COMMON_SET_SETTING,
                    [cmd for cmd, _payload in fc.sent],
                )

    def test_readback_must_match(self):
        from d4d_fc.control_write import ControlWriteBlocked

        fc = _ControlFakeFc(verify=False)
        with self.assertRaises(ControlWriteBlocked):
            self.apply(fc)

    def test_restore_64_is_verified_without_grant_confirmation(self):
        fc = _ControlFakeFc(mask=67, throttle=1960)
        result = self.apply(fc, target=64, confirmation=None)
        self.assertTrue(result["verified"])
        self.assertEqual(result["after"], 64)

    def test_backup_precedes_mutation_and_contains_cli_restore(self):
        from d4d_fc.control_write import write_ram_setting

        fc = _ControlFakeFc()
        with tempfile.TemporaryDirectory() as td:
            result = write_ram_setting(
                fc,
                "msp_override_channels",
                67,
                confirmation=self.confirmation,
                writes_enabled=True,
                backup_dir=td,
            )
            backup = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(backup["previous_value"], 64)
        self.assertEqual(
            backup["cli_restore"],
            "set msp_override_channels = 64",
        )

    def test_source_has_one_write_command_and_no_persistent_or_actuator_commands(self):
        import d4d_fc.control_write as writer

        source = Path(writer.__file__).read_text(encoding="utf-8")
        self.assertIn(
            writer.C.MSP2_COMMON_SET_SETTING,
            writer.C.DANGEROUS_COMMANDS,
        )
        self.assertEqual(
            writer.ALLOWED_WRITE_COMMANDS,
            {writer.C.MSP2_COMMON_SET_SETTING},
        )
        for forbidden in (
            "MSP_SET_RAW_RC",
            "MSP_EEPROM_WRITE",
            "MSP_SET_PID",
            "MSP2_INAV_SET_MIXER",
            "MSP_SET_MODE_RANGE",
            "MSP_SET_MOTOR",
            "MSP_REBOOT",
            "MSP_RESET_CONF",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

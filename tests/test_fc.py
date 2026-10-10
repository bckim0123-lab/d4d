"""d4d_fc 테스트.

실제 장치(INAV 9.1.0 / MICOAIR743V2)에서 캡처한 응답(tests/fixtures_fc.json)으로
디코더를 검증한다. 하드웨어 없이 실행된다.
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4d_fc import codes as C  # noqa: E402
from d4d_fc import decode as D  # noqa: E402
from d4d_fc.msp import encode_v1, encode_v2  # noqa: E402
from d4d_fc.readiness import FAIL, OK, evaluate  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures_fc.json").read_text(encoding="utf-8"))
SYMPTOM_KEYS = ("highfreq", "wobble", "bounce", "propwash", "poshold", "firstflight")


def f(name: str) -> bytes:
    h = FIX["frames"][name]
    if h is None:
        raise unittest.SkipTest(f"{name} 고정값 없음")
    return bytes.fromhex(h)


def s(name: str) -> bytes:
    return bytes.fromhex(FIX["setting_info"][name])


class FramingTests(unittest.TestCase):
    def test_v1_matches_known_frame(self):
        # 실제 장치에 보냈을 때 응답이 온 요청 프레임
        self.assertEqual(encode_v1(C.MSP_FC_VARIANT), bytes.fromhex("244d3c000202"))

    def test_v2_checksum(self):
        fr = encode_v2(C.MSP2_COMMON_SETTING_INFO, b"looptime\x00")
        self.assertTrue(fr.startswith(b"$X<"))
        body = fr[3:-1]
        crc = 0
        for b in body:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        self.assertEqual(fr[-1], crc)
        self.assertEqual(struct.unpack_from("<H", fr, 4)[0], C.MSP2_COMMON_SETTING_INFO)

    def test_v1_rejects_oversized(self):
        with self.assertRaises(ValueError):
            encode_v1(C.MSP2_COMMON_SETTING, b"x" * 300)


class IdentityTests(unittest.TestCase):
    def test_variant_and_version(self):
        self.assertEqual(f("MSP_FC_VARIANT").decode(), "INAV")
        self.assertEqual(D.fc_version(f("MSP_FC_VERSION"))["version"], "9.1.0")
        self.assertEqual(D.api_version(f("MSP_API_VERSION"))["api"], "2.5")

    def test_board(self):
        b = D.board_info(f("MSP_BOARD_INFO"))
        self.assertEqual(b["board_id"], "M7V2")
        self.assertEqual(b["board_name"], "MICOAIR743V2")

    def test_uid_is_hex(self):
        u = D.uid(f("MSP_UID"))["uid"]
        self.assertEqual(len(u), 24)
        int(u, 16)


class StatusTests(unittest.TestCase):
    def test_inav_status(self):
        st = D.inav_status(f("MSP2_INAV_STATUS"))
        self.assertEqual(st["i2c_errors"], 50)
        self.assertEqual(st["cpu_load_pct"], 2)
        self.assertEqual(st["sensors_mask"], 0x87)
        self.assertEqual(st["sensors"], ["가속도계", "기압계", "지자기", "온도계"])
        self.assertFalse(st["armed"])
        self.assertEqual(st["arming_flags"], 0x10040000)
        self.assertEqual(set(st["arming"]), {"DISABLED_RC_LINK", "DISABLED_NO_PREARM"})
        self.assertIn("수신기 신호 없음", st["arm_blockers"])
        self.assertEqual(st["pid_profile"], 1)

    def test_status_consistent_with_inav_status(self):
        a = D.status(f("MSP_STATUS"))
        b = D.inav_status(f("MSP2_INAV_STATUS"))
        self.assertEqual(a["sensors"], b["sensors"])
        self.assertEqual(a["i2c_errors"], b["i2c_errors"])

    def test_sensor_config_shows_no_flow_or_rangefinder(self):
        sc = D.sensor_config(f("MSP_SENSOR_CONFIG"))
        self.assertEqual(sc["acc"], 7)
        self.assertEqual(sc["rangefinder"], 0)
        self.assertEqual(sc["opflow"], 0)

    def test_short_payload_returns_raw(self):
        self.assertIn("raw", D.inav_status(b"\x01\x02"))
        self.assertIn("raw", D.attitude(b""))
        self.assertIn("raw", D.raw_gps(b"\x00"))


class LiveDataTests(unittest.TestCase):
    def test_attitude_and_altitude(self):
        a = D.attitude(f("MSP_ATTITUDE"))
        self.assertTrue(-180 <= a["roll_deg"] <= 180)
        self.assertTrue(0 <= a["yaw_deg"] <= 360)
        al = D.altitude(f("MSP_ALTITUDE"))
        self.assertIn("est_alt_m", al)

    def test_analog_usb_powered(self):
        v = D.inav_analog(f("MSP2_INAV_ANALOG"))
        self.assertLess(v["vbat_V"], 1.0)  # USB 전원만 연결된 상태
        self.assertEqual(v["cell_count"], 0)

    def test_rc_18_channels(self):
        r = D.rc(f("MSP_RC"))
        self.assertEqual(r["channel_count"], 18)
        self.assertEqual(len(r["channels_us"]), 18)

    def test_rangefinder_and_opflow_report_nothing(self):
        self.assertEqual(D.sonar_altitude(f("MSP_SONAR_ALTITUDE"))["rangefinder_cm"], 0)
        self.assertEqual(D.opflow(f("MSP2_INAV_OPTICAL_FLOW"))["quality"], 0)


class ModeTests(unittest.TestCase):
    def test_inav_91_permanent_ids_for_control_modes(self):
        """INAV 9.1 fc_msp_box.c의 permanentId와 일치해야 한다."""
        self.assertEqual(C.BOX_NAMES[49], "LOITER CHANGE")
        self.assertEqual(C.BOX_NAMES[50], "MSP RC OVERRIDE")
        self.assertEqual(C.BOX_NAMES[51], "PREARM")
        self.assertEqual(C.BOX_NAMES[52], "TURTLE")
        self.assertEqual(C.BOX_NAMES[61], "MULTI FUNCTION")
        self.assertEqual(C.BOX_NAMES[63], "MIXER TRANSITION")
        self.assertEqual(C.BOX_NAMES[67], "GIMBAL CENTER")

    def test_mode_ranges(self):
        r = D.mode_ranges(f("MSP_MODE_RANGES"))
        self.assertEqual(len(r), 3)
        by = {x["mode"]: x for x in r}
        self.assertEqual(by["ARM"]["aux_channel"], 2)
        self.assertEqual(by["ARM"]["range_us"], [1700, 2100])
        self.assertEqual(by["PREARM"]["aux_channel"], 1)
        self.assertEqual(by["ANGLE"]["range_us"], [1300, 1700])

    def test_active_boxes(self):
        ids = list(f("MSP_BOXIDS"))
        active = D.active_boxes(f("MSP_ACTIVEBOXES"), ids)
        self.assertEqual(active, ["ANGLE", "FAILSAFE"])

    def test_boxids_lack_gps_modes(self):
        """GPS가 감지되지 않으므로 GPS 의존 모드는 목록에 없어야 한다."""
        names = [C.BOX_NAMES.get(i, str(i)) for i in f("MSP_BOXIDS")]
        for gps_mode in ("NAV RTH", "NAV POSHOLD", "NAV WP", "GCS NAV"):
            self.assertNotIn(gps_mode, names)
        self.assertIn("ARM", names)
        self.assertIn("MSP RC OVERRIDE", names)

    def test_arming_blockers_match_configured_modes(self):
        """PREARM이 스위치에 할당되어 있고 비활성이므로 NO_PREARM 차단이 일관적이다."""
        ranges = D.mode_ranges(f("MSP_MODE_RANGES"))
        st = D.inav_status(f("MSP2_INAV_STATUS"))
        self.assertIn("PREARM", [r["mode"] for r in ranges])
        self.assertIn("DISABLED_NO_PREARM", st["arming"])
        self.assertNotIn("PREARM", D.active_boxes(f("MSP_ACTIVEBOXES"), list(f("MSP_BOXIDS"))))


class HardwareTests(unittest.TestCase):
    def test_serial_ports(self):
        ports = D.serial_config_v2(f("MSP2_COMMON_SERIAL_CONFIG"))
        self.assertEqual(len(ports), 9)
        by = {p["port_label"]: p for p in ports}
        self.assertEqual(by["USB VCP"]["functions"], ["MSP"])
        self.assertEqual(by["UART3"]["functions"], ["GPS"])
        self.assertEqual(by["UART6"]["functions"], ["RX_SERIAL"])
        self.assertEqual(by["UART4"]["functions"], ["(없음)"])
        self.assertEqual(by["UART5"]["functions"], ["(없음)"])
        # 광류/거리 센서용 포트가 아직 없다는 것이 이 프로젝트의 핵심 결론
        for p in ports:
            self.assertNotIn("OPTICAL_FLOW", p["functions"])
            self.assertNotIn("RANGEFINDER", p["functions"])

    def test_motor_mixer_is_quad_x(self):
        m = D.motor_mixer(f("MSP2_COMMON_MOTOR_MIXER"))
        self.assertEqual(len(m), 4)
        for mot in m:
            self.assertEqual(mot["throttle"], 1.0)
            self.assertIn(mot["roll"], (-1.0, 1.0))
            self.assertIn(mot["pitch"], (-1.0, 1.0))
            self.assertIn(mot["yaw"], (-1.0, 1.0))
        # Quad X: roll/pitch 조합 4개가 모두 달라야 한다
        self.assertEqual(len({(x["roll"], x["pitch"]) for x in m}), 4)

    def test_failsafe_is_drop(self):
        fs = D.failsafe_config(f("MSP_FAILSAFE_CONFIG"))
        self.assertEqual(fs["procedure_code"], 1)
        self.assertTrue(fs["procedure"].startswith("DROP"))
        self.assertEqual(fs["delay_ds"], 5)


class SettingTests(unittest.TestCase):
    def test_numeric(self):
        x = D.setting_info(s("looptime"))
        self.assertEqual(x["name"], "looptime")
        self.assertEqual(x["index"], 0)
        self.assertEqual(x["type"], "uint16")
        self.assertEqual(x["value"], 500)
        self.assertEqual(x["section"], "master")
        self.assertNotIn("decode_warning", x)

    def test_lookup(self):
        x = D.setting_info(s("nav_extra_arming_safety"))
        self.assertEqual(x["lookup"], ["ON", "ALLOW_BYPASS"])
        self.assertEqual(x["value_name"], "ALLOW_BYPASS")

    def test_platform_is_multirotor(self):
        x = D.setting_info(s("platform_type"))
        self.assertEqual(x["value_name"], "MULTIROTOR")

    def test_float_type(self):
        """타입 5는 float. 0x3F800000 == 1.0 으로 확인된다."""
        x = D.setting_info(s("inav_w_xy_flow_p"))
        self.assertEqual(x["type"], "float")
        self.assertEqual(x["value"], 1.0)
        y = D.setting_info(s("opflow_scale"))
        self.assertEqual(y["value"], 10.5)

    def test_string_type(self):
        x = D.setting_info(s("name"))
        self.assertEqual(x["type"], "string")
        self.assertEqual(x["value"], "")
        self.assertNotIn("decode_warning", x)

    def test_signed(self):
        x = D.setting_info(s("align_board_yaw"))
        self.assertEqual(x["type"], "int16")
        self.assertEqual(x["min"], -1800)
        self.assertEqual(x["max"], 3600)

    def test_profile_section(self):
        x = D.setting_info(s("mc_p_pitch"))
        self.assertEqual(x["section"], "profile")
        self.assertEqual(x["profile_count"], 3)

    def test_msp_override_is_disabled(self):
        x = D.setting_info(s("msp_override_channels"))
        self.assertEqual(x["value"], 0)

    def test_no_decode_warnings_in_fixtures(self):
        for name in FIX["setting_info"]:
            x = D.setting_info(s(name))
            self.assertNotIn("decode_warning", x, f"{name}: {x.get('decode_warning')}")


class ReadinessTests(unittest.TestCase):
    """실제 조사 결과를 사용한다(있을 때만)."""

    @classmethod
    def setUpClass(cls):
        p = ROOT / "out" / "fc" / "fc_inspect.json"
        if not p.exists():
            raise unittest.SkipTest("out/fc/fc_inspect.json 이 없습니다 (먼저 inspect 실행)")
        cls.data = json.loads(p.read_text(encoding="utf-8"))

    def test_flags_blockers(self):
        checks = {c.name: c for c in evaluate(self.data)}
        self.assertEqual(checks["MTF-01P 광류/거리 센서"].level, FAIL)
        self.assertEqual(checks["XIAO -> FC 자동 제어 경로"].level, FAIL)
        self.assertEqual(checks["Failsafe 동작"].level, FAIL)
        self.assertEqual(checks["기체 형식"].level, OK)
        self.assertTrue(checks["MTF-01P 광류/거리 센서"].action)

    def test_all_settings_read(self):
        s = self.data["settings"]
        self.assertEqual(s["count"], s["expected"])
        self.assertEqual(s["failed_indices"], [])


class CurrentTopologyReadinessTests(unittest.TestCase):
    def data(self, *, sensors=None, uart4_functions=None):
        return {
            "status": {
                "inav_status": {
                    "sensors": sensors
                    if sensors is not None
                    else ["가속도계", "레인지파인더", "광류(OPFLOW)"],
                    "arming": [],
                    "arm_blockers": [],
                }
            },
            "settings": {
                "items": [
                    {"name": "platform_type", "value_name": "MULTIROTOR"},
                    {"name": "acc_hardware", "value_name": "BMI088"},
                    {"name": "baro_hardware", "value_name": "SPL06"},
                    {"name": "mag_hardware", "value_name": "QMC5883"},
                    {"name": "opflow_hardware", "value_name": "MSP"},
                    {"name": "rangefinder_hardware", "value_name": "MSP"},
                    {"name": "msp_override_channels", "value": 64},
                    {"name": "inav_allow_dead_reckoning", "value_name": "OFF"},
                    {"name": "inav_w_xy_flow_p", "value": 1.0},
                    {"name": "inav_w_xy_flow_v", "value": 2.0},
                    {"name": "opflow_scale", "value": 10.5},
                    {"name": "inav_max_surface_altitude", "value": 200},
                    {"name": "serialrx_provider", "value_name": "IBUS"},
                    {"name": "failsafe_procedure", "value_name": "DROP"},
                ]
            },
            "modes": {
                "configured_mode_switches": [
                    {
                        "permanent_id": 50,
                        "mode": C.BOX_NAMES[50],
                        "aux_channel": 2,
                        "range_us": [900, 1700],
                    }
                ]
            },
            "hardware": {
                "motor_count": 4,
                "serial_ports": [
                    {"port_label": "USB VCP", "functions": ["MSP"]},
                    {
                        "port_label": "UART4",
                        "functions": uart4_functions
                        if uart4_functions is not None
                        else ["MSP"],
                    },
                    {"port_label": "UART5", "functions": ["MSP"]},
                    {"port_label": "UART6", "functions": ["RX_SERIAL"]},
                ],
                "failsafe": {"procedure": "DROP", "delay_ds": 5},
            },
        }

    def test_msp_uart_sensor_topology_is_ready(self):
        checks = {c.name: c for c in evaluate(self.data())}
        self.assertEqual(checks["MTF-01P 광류/거리 센서"].level, OK)
        self.assertEqual(checks["XIAO -> FC 자동 제어 경로"].level, OK)

    def test_msp_sensor_topology_requires_detected_sensors(self):
        checks = {
            c.name: c
            for c in evaluate(self.data(sensors=["가속도계", "레인지파인더"]))
        }
        self.assertEqual(checks["MTF-01P 광류/거리 센서"].level, FAIL)
        self.assertIn("광류 미감지", checks["MTF-01P 광류/거리 센서"].detail)

    def test_msp_sensor_topology_requires_non_usb_msp_uart(self):
        data = self.data(uart4_functions=["(없음)"])
        data["hardware"]["serial_ports"][2]["functions"] = ["(없음)"]
        checks = {c.name: c for c in evaluate(data)}
        self.assertEqual(checks["MTF-01P 광류/거리 센서"].level, FAIL)
        self.assertIn("MSP UART 없음", checks["MTF-01P 광류/거리 센서"].detail)


SNAP = ROOT / "snapshots" / "fc_snapshot.json"


class ReplayTests(unittest.TestCase):
    """케이블을 뽑은 상태에서도 개발이 가능한지 검증한다."""

    @classmethod
    def setUpClass(cls):
        if not SNAP.exists():
            raise unittest.SkipTest("fc_snapshot.json 이 없습니다 (먼저 snapshot 실행)")
        from d4d_fc.replay import ReplayClient

        cls.ReplayClient = ReplayClient

    def test_serves_frames_without_serial_port(self):
        import d4d_fc.replay as rp

        self.assertNotIn("serial", Path(rp.__file__).read_text(encoding="utf-8"))
        with self.ReplayClient.from_file(SNAP) as fc:
            self.assertEqual(fc.request(C.MSP_FC_VARIANT), b"INAV")
            self.assertEqual(D.fc_version(fc.request(C.MSP_FC_VERSION))["version"], "9.1.0")

    def test_full_inspect_runs_offline(self):
        from d4d_fc.inspect import gather
        from d4d_fc.readiness import FAIL, evaluate

        with self.ReplayClient.from_file(SNAP) as fc:
            data = gather(fc, with_settings=True)
        self.assertEqual(data["identity"]["variant"], "INAV")
        self.assertEqual(data["settings"]["count"], 666)
        self.assertEqual(data["hardware"]["motor_count"], 4)
        self.assertEqual(len(data["hardware"]["serial_ports"]), 9)
        checks = {c.name: c for c in evaluate(data)}
        self.assertEqual(checks["MTF-01P 광류/거리 센서"].level, FAIL)

    def test_matches_live_inspection(self):
        """재생 결과가 실제 장치 조사 결과와 같은 결론을 내야 한다."""
        live_path = ROOT / "out" / "fc" / "fc_inspect.json"
        if not live_path.exists():
            self.skipTest("실장치 조사 결과 없음")
        live = json.loads(live_path.read_text(encoding="utf-8"))
        from d4d_fc.inspect import gather

        with self.ReplayClient.from_file(SNAP) as fc:
            rep = gather(fc, with_settings=False)
        self.assertEqual(rep["identity"]["board"]["board_name"], live["identity"]["board"]["board_name"])
        self.assertEqual(rep["modes"]["configured_mode_switches"], live["modes"]["configured_mode_switches"])
        self.assertEqual(rep["hardware"]["serial_ports"], live["hardware"]["serial_ports"])

    def test_setting_lookup_by_name_and_index(self):
        with self.ReplayClient.from_file(SNAP) as fc:
            self.assertEqual(fc.setting("platform_type")["value_name"], "MULTIROTOR")
            self.assertEqual(fc.setting("looptime")["index"], 0)
            raw = fc.request(C.MSP2_COMMON_SETTING_INFO, b"\x00" + struct.pack("<H", 0))
            self.assertEqual(D.setting_info(raw)["name"], "looptime")

    def test_live_series_advances_and_loops(self):
        with self.ReplayClient.from_file(SNAP) as fc:
            n = fc.live_length
            self.assertGreaterEqual(n, 10)
            seen = [fc.request(C.MSP_ATTITUDE) for _ in range(n + 3)]
            self.assertEqual(seen[0], seen[n])  # 끝나면 처음으로 돌아간다

    def test_missing_command_raises(self):
        from d4d_fc.msp import MspError

        with self.ReplayClient.from_file(SNAP) as fc:
            with self.assertRaises(MspError):
                fc.request(0x7FFE)
            self.assertIsNone(fc.try_request(0x7FFE))

    def test_replay_refuses_write_commands(self):
        from d4d_fc.msp import MspError

        with self.ReplayClient.from_file(SNAP) as fc:
            for cmd in (C.MSP_SET_RAW_RC, C.MSP_EEPROM_WRITE, C.MSP2_COMMON_SET_SETTING):
                with self.assertRaises(MspError):
                    fc.request(cmd, b"\x00")

    def test_rejects_wrong_file(self):
        with self.assertRaises(FileNotFoundError):
            self.ReplayClient.from_file(ROOT / "없는파일.json")
        with self.assertRaises(ValueError):
            self.ReplayClient.from_file(ROOT / "tests" / "fixtures_fc.json")


class SnapshotTests(unittest.TestCase):
    def test_allowlist_blocks_dangerous_commands(self):
        from d4d_fc.snapshot import NEVER_SEND, SAFE_GET, UnsafeCommand, _guard

        safe = {c for _, c, _ in SAFE_GET}
        # 허용목록과 차단목록은 겹치면 안 된다
        self.assertEqual(safe & NEVER_SEND, set())
        for cmd in (205, 206, 208, 250, 68, 72, C.MSP_SET_RAW_RC, C.MSP2_COMMON_SET_SETTING):
            with self.assertRaises(UnsafeCommand, msg=f"cmd {cmd}"):
                _guard(cmd)
        _guard(C.MSP_FC_VARIANT)
        _guard(C.MSP2_COMMON_SETTING_INFO)

    def test_unknown_command_also_blocked(self):
        from d4d_fc.snapshot import UnsafeCommand, _guard

        with self.assertRaises(UnsafeCommand):
            _guard(199)

    def test_settings_dump_lines(self):
        if not SNAP.exists():
            self.skipTest("스냅샷 없음")
        from d4d_fc.snapshot import settings_dump_lines

        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        lines = settings_dump_lines(snap)
        joined = "\n".join(lines)
        self.assertIn("set platform_type = MULTIROTOR", joined)
        self.assertIn("set failsafe_procedure = DROP", joined)
        self.assertIn("set opflow_hardware = NONE", joined)
        items = snap["settings"]["items"]
        # 값이 빈 문자열인 설정만 주석으로 빠지고, 나머지는 모두 set 줄이 된다
        empty_strings = [i for i in items if i.get("type") == "string" and not i.get("value")]
        self.assertEqual(sum(1 for x in lines if x.startswith("set ")),
                         len(items) - len(empty_strings))
        self.assertEqual(len(empty_strings), 3)

    def test_snapshot_has_raw_bytes_for_every_setting(self):
        if not SNAP.exists():
            self.skipTest("스냅샷 없음")
        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        s = snap["settings"]
        self.assertEqual(len(s["raw_by_index"]), s["count"])
        self.assertEqual(s["failed_indices"], [])
        self.assertTrue(snap["read_only"])
        # 모든 원본이 다시 해석 가능해야 한다
        for idx, hx in s["raw_by_index"].items():
            x = D.setting_info(bytes.fromhex(hx))
            self.assertEqual(x["index"], int(idx))
            self.assertNotIn("decode_warning", x)


class TuneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SNAP.exists():
            raise unittest.SkipTest("스냅샷 없음")
        from d4d_fc.tune import read_tuning

        cls.snap = json.loads(SNAP.read_text(encoding="utf-8"))
        cls.t = read_tuning(cls.snap)

    def test_detects_ez_tune_enabled(self):
        self.assertTrue(self.t.ez_enabled)
        self.assertEqual(self.t.val("ez_filter_hz"), 110)
        self.assertEqual(self.t.val("ez_damping"), 115)

    def test_blackbox_device_comes_from_settings_not_frame(self):
        """MSP_BLACKBOX_CONFIG(80)은 INAV 9에서 지원되지 않아 항상 0을 돌려준다.
        그 0을 device로 읽으면 로깅이 꺼진 것으로 오진한다."""
        from d4d_fc.tune import blackbox_status

        self.assertEqual(bytes.fromhex(self.snap["frames"]["MSP_BLACKBOX_CONFIG"]["hex"]), b"\x00\x00\x00\x00")
        self.assertEqual(self.t.blackbox_device, 2)  # SDCARD
        ok, lines = blackbox_status(self.t)
        self.assertTrue(ok)
        self.assertIn("SDCARD", lines[0])

    def test_detects_no_flight_data(self):
        from d4d_fc.tune import blockers

        self.assertFalse(self.t.was_ever_armed)
        self.assertEqual(self.t.sdcard["state"], 4)  # SD카드는 준비됨
        b = blockers(self.t)
        self.assertTrue(any("무장" in x for x in b))
        self.assertFalse(any("블랙박스" in x for x in b))  # 로깅은 이미 켜져 있다

    def test_blackbox_off_is_reported(self):
        from d4d_fc.tune import blackbox_status, blockers, read_tuning

        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        for x in snap["settings"]["items"]:
            if x["name"] == "blackbox_device":
                x["value"], x["value_name"] = 0, "NONE"
        t2 = read_tuning(snap)
        ok, _ = blackbox_status(t2)
        self.assertFalse(ok)
        self.assertTrue(any("블랙박스" in x for x in blockers(t2)))

    def test_failsafe_drop_is_a_blocker(self):
        from d4d_fc.tune import blockers

        self.assertEqual(self.t.val("failsafe_procedure"), "DROP")
        self.assertTrue(any("DROP" in x for x in blockers(self.t)))

    def test_firstflight_includes_procedure_and_no_pid_guessing(self):
        from d4d_fc.tune import propose

        p = propose(self.t, "firstflight")
        self.assertTrue(p.procedure)
        self.assertEqual([c.setting for c in p.changes], ["ez_rate"])
        self.assertEqual(p.changes[0].proposed, 100)
        self.assertTrue(any("추측으로 바꾸지 않는다" in n for n in p.notes))

    def test_ez_symptoms_touch_sliders_not_raw_pids(self):
        from d4d_fc.tune import propose

        for sym in ("highfreq", "wobble", "bounce", "propwash"):
            p = propose(self.t, sym)
            self.assertTrue(p.changes, sym)
            for c in p.changes:
                self.assertTrue(c.setting.startswith("ez_"), f"{sym}: {c.setting}")

    def test_poshold_uses_nav_pids(self):
        from d4d_fc.tune import propose

        p = propose(self.t, "poshold")
        self.assertTrue(all(c.setting.startswith("nav_mc_") for c in p.changes))
        self.assertTrue(any("항법" in n for n in p.notes))

    def test_proposals_stay_in_range(self):
        from d4d_fc.tune import propose

        for sym in SYMPTOM_KEYS:
            for c in propose(self.t, sym).changes:
                s = self.t.settings[c.setting]
                self.assertGreaterEqual(c.proposed, s["min"], f"{sym}/{c.setting}")
                self.assertLessEqual(c.proposed, s["max"], f"{sym}/{c.setting}")
                self.assertNotEqual(c.proposed, c.current)

    def test_directions_are_correct(self):
        from d4d_fc.tune import propose

        hf = {c.setting: c for c in propose(self.t, "highfreq").changes}
        self.assertLess(hf["ez_filter_hz"].proposed, hf["ez_filter_hz"].current)
        self.assertLess(hf["ez_damping"].proposed, hf["ez_damping"].current)
        bo = {c.setting: c for c in propose(self.t, "bounce").changes}
        self.assertGreater(bo["ez_damping"].proposed, bo["ez_damping"].current)
        wo = {c.setting: c for c in propose(self.t, "wobble").changes}
        self.assertLess(wo["ez_response"].proposed, wo["ez_response"].current)

    def test_raw_pid_path_when_ez_disabled(self):
        from d4d_fc.tune import propose, read_tuning

        snap = json.loads(SNAP.read_text(encoding="utf-8"))
        for x in snap["settings"]["items"]:
            if x["name"] == "ez_enabled":
                x["value"], x["value_name"] = 0, "OFF"
        t2 = read_tuning(snap)
        self.assertFalse(t2.ez_enabled)
        p = propose(t2, "highfreq")
        names = {c.setting for c in p.changes}
        self.assertIn("mc_d_roll", names)
        self.assertNotIn("ez_damping", names)

    def test_unknown_symptom(self):
        from d4d_fc.tune import propose

        with self.assertRaises(ValueError):
            propose(self.t, "없는증상")

    def test_report_mentions_ez_override_warning(self):
        from d4d_fc.tune import propose, render_report

        md = "\n".join(render_report(self.t, propose(self.t, "highfreq")))
        self.assertIn("유지되지 않습니다", md)
        self.assertIn("set ez_filter_hz = 90", md)
        self.assertIn("한 번에 하나씩", md)

    def test_tune_module_never_writes(self):
        import d4d_fc.tune as tn

        src = Path(tn.__file__).read_text(encoding="utf-8")
        for bad in ("MSP_SET", "SET_SETTING", "EEPROM", "request(", "serial"):
            self.assertNotIn(bad, src, f"tune 모듈이 {bad}를 참조합니다")


class _FakeFc:
    """모드 범위 쓰기만 흉내내는 가짜 FC."""

    def __init__(self, entries, armed=False, boxids=(0, 51, 61, 1, 2), accept=True):
        self.entries = [list(e) for e in entries]
        self.armed = armed
        self.boxids = list(boxids)
        self.accept = accept
        self.sent: list[tuple[int, bytes]] = []
        self.eeprom: list[list[int]] | None = None

    def request(self, cmd, payload=b"", **kw):
        import d4d_fc.codes as C
        from d4d_fc.msp import MspError

        self.sent.append((cmd, payload))
        if cmd == C.MSP_MODE_RANGES:
            return b"".join(bytes(e) for e in self.entries)
        if cmd == C.MSP_SET_MODE_RANGE:
            if not self.accept:
                raise MspError("거부")
            slot, pid, aux, s, t = payload
            while len(self.entries) <= slot:
                self.entries.append([0, 0, 0, 0])
            self.entries[slot] = [pid, aux, s, t]
            return b""
        if cmd == C.MSP_EEPROM_WRITE:
            self.eeprom = [list(e) for e in self.entries]
            return b""
        if cmd == C.MSP_BOXIDS:
            return bytes(self.boxids)
        if cmd == C.MSP_ACTIVEBOXES:
            mask = 0
            for i, pid in enumerate(self.boxids):
                for e in self.entries:
                    if e[0] == pid and e[2] < e[3]:
                        mask |= 1 << i
            return mask.to_bytes(8, "little")
        raise MspError(f"지원 안 함 {cmd}")

    def try_request(self, cmd, payload=b"", **kw):
        import d4d_fc.codes as C

        if cmd == C.MSP2_INAV_STATUS:
            flags = (1 << 2) if self.armed else 0
            return struct.pack("<HHHHB I", 500, 0, 0x7F, 10, 0, flags) + b"\x00\x00"
        try:
            return self.request(cmd, payload)
        except Exception:
            return None


# 실제 기체에서 읽은 값: ARM(aux1), PREARM(aux0), ANGLE(aux2 1300~1700)
REAL_ENTRIES = [(0, 1, 32, 48), (51, 0, 32, 48), (1, 2, 16, 32), (0, 0, 0, 0), (0, 0, 0, 0)]


class WriteTests(unittest.TestCase):
    def test_step_conversion_matches_firmware_formula(self):
        from d4d_fc.write import step_to_us, us_to_step

        self.assertEqual(step_to_us(0), 900)
        self.assertEqual(step_to_us(48), 2100)
        self.assertEqual(us_to_step(1300), 16)
        self.assertEqual(us_to_step(1700), 32)
        self.assertEqual(us_to_step(5000), 48)  # 범위를 벗어나면 잘린다
        self.assertEqual(us_to_step(0), 0)

    def test_plan_uses_full_span_and_free_channel(self):
        from d4d_fc.write import plan_angle_always

        p = plan_angle_always(REAL_ENTRIES)
        self.assertEqual(p.slot, 2)                  # 기존 ANGLE 슬롯을 대체
        self.assertEqual(p.after[0], 1)              # ANGLE 영구 ID
        self.assertEqual(p.after[1], 3)              # aux0~2는 사용 중 -> 3
        self.assertEqual((p.after[2], p.after[3]), (0, 48))  # 전 구간
        self.assertEqual(p.before, (1, 2, 16, 32))

    def test_plan_payload_layout(self):
        from d4d_fc.write import plan_angle_always

        p = plan_angle_always(REAL_ENTRIES, aux_index=3)
        self.assertEqual(p.payload, bytes([2, 1, 3, 0, 48]))

    def test_rerun_is_a_noop_and_does_not_move_channel(self):
        """멱등하지 않으면 다시 실행할 때마다 '빈 채널'의 정의가 바뀌어
        ANGLE이 채널 사이를 왕복한다. 실제 기체에서 한 번 겪었다."""
        from d4d_fc.write import plan_angle_always

        done = [(0, 1, 32, 48), (51, 0, 32, 48), (1, 3, 0, 48), (0, 0, 0, 0)]
        p = plan_angle_always(done)
        self.assertTrue(p.noop)
        self.assertEqual(p.after, (1, 3, 0, 48))
        self.assertEqual(p.before, p.after)
        self.assertTrue(any("이미" in n for n in p.notes))

    def test_noop_is_not_applied(self):
        from d4d_fc.write import plan_angle_always

        done = [(0, 1, 32, 48), (51, 0, 32, 48), (1, 3, 0, 48), (0, 0, 0, 0)]
        fc = _FakeFc(done)
        p = plan_angle_always(done)
        self.assertTrue(p.noop)
        self.assertEqual(fc.sent, [])  # 계획 단계에서 아무것도 보내지 않는다

    def test_explicit_aux_overrides_noop(self):
        from d4d_fc.write import plan_angle_always

        done = [(0, 1, 32, 48), (51, 0, 32, 48), (1, 2, 0, 48), (0, 0, 0, 0)]
        p = plan_angle_always(done, aux_index=3)
        self.assertFalse(p.noop)
        self.assertEqual(p.after, (1, 3, 0, 48))

    def test_keep_existing_uses_empty_slot(self):
        from d4d_fc.write import plan_angle_always

        p = plan_angle_always(REAL_ENTRIES, keep_existing=True)
        self.assertEqual(p.slot, 3)
        self.assertIsNone(p.before)
        self.assertTrue(any("OR" in n for n in p.notes))

    def _apply(self, fc, plan, **kw):
        """백업 파일을 임시 폴더로 돌려서 실행한다."""
        from d4d_fc.write import apply

        with tempfile.TemporaryDirectory() as td:
            with mock.patch("d4d_fc.write.backup",
                            side_effect=lambda e, out_dir=td: Path(td) / "b.json"):
                return apply(fc, plan, **kw)

    def test_apply_writes_verifies_and_activates(self):
        from d4d_fc.write import plan_angle_always

        fc = _FakeFc(REAL_ENTRIES)
        p = plan_angle_always(REAL_ENTRIES, aux_index=3)
        res = self._apply(fc, p)
        self.assertTrue(res["verified"])
        self.assertEqual(res["actual"], [1, 3, 0, 48])
        self.assertIn("ANGLE", res["active_modes"])
        self.assertFalse(res["saved"])
        self.assertIsNone(fc.eeprom)  # 저장은 요청 없이는 하지 않는다

    def test_apply_refuses_when_armed(self):
        from d4d_fc.write import WriteBlocked, apply, plan_angle_always

        fc = _FakeFc(REAL_ENTRIES, armed=True)
        p = plan_angle_always(REAL_ENTRIES, aux_index=3)
        with self.assertRaises(WriteBlocked):
            apply(fc, p)
        self.assertNotIn(C.MSP_SET_MODE_RANGE, [c for c, _ in fc.sent])

    def test_apply_does_not_save_when_verification_fails(self):
        from d4d_fc.write import WriteBlocked, plan_angle_always

        fc = _FakeFc(REAL_ENTRIES, accept=False)
        p = plan_angle_always(REAL_ENTRIES, aux_index=3)
        with self.assertRaises(WriteBlocked):
            self._apply(fc, p, save=True)
        self.assertIsNone(fc.eeprom)

    def test_save_writes_eeprom_only_when_asked(self):
        from d4d_fc.write import plan_angle_always

        fc = _FakeFc(REAL_ENTRIES)
        p = plan_angle_always(REAL_ENTRIES, aux_index=3)
        res = self._apply(fc, p, save=True)
        self.assertTrue(res["saved"])
        self.assertEqual(fc.eeprom[2], [1, 3, 0, 48])

    def test_guard_blocks_everything_except_two_commands(self):
        from d4d_fc.write import ALLOWED_WRITES, WriteBlocked, _guard

        self.assertEqual(ALLOWED_WRITES, {C.MSP_SET_MODE_RANGE, C.MSP_EEPROM_WRITE})
        for cmd in (C.MSP_SET_RAW_RC, C.MSP_SET_PID, C.MSP2_COMMON_SET_SETTING,
                    C.MSP2_INAV_SET_MIXER, 205, 206, 208, 68):
            with self.assertRaises(WriteBlocked, msg=f"cmd {cmd}"):
                _guard(cmd)

    def test_backup_contains_restorable_cli_lines(self):
        from d4d_fc.write import backup

        with tempfile.TemporaryDirectory() as td:
            p = backup(REAL_ENTRIES, out_dir=td)
            data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("aux 2 1 2 1300 1700 0", data["cli_restore"])
        self.assertEqual(len(data["entries"]), len(REAL_ENTRIES))

    def test_write_module_never_touches_motors_or_settings(self):
        import d4d_fc.write as W

        src = Path(W.__file__).read_text(encoding="utf-8")
        for bad in ("SET_RAW_RC", "SET_SETTING", "SET_PID", "SET_MIXER",
                    "RESET_CONF", "CALIBRATION", "REBOOT"):
            self.assertNotIn(bad, src, f"write 모듈이 {bad} 를 참조합니다")


class SafetyTests(unittest.TestCase):
    def test_write_commands_are_catalogued_not_used(self):
        """도구는 읽기 전용이다. 쓰기 명령 코드는 목록으로만 존재해야 한다."""
        import d4d_fc.inspect as insp
        import d4d_fc.readiness as rd

        src = (Path(insp.__file__).read_text(encoding="utf-8")
               + Path(rd.__file__).read_text(encoding="utf-8"))
        for name in ("MSP_SET_RAW_RC", "MSP_EEPROM_WRITE", "MSP_SET_PID",
                     "MSP2_COMMON_SET_SETTING", "MSP2_INAV_SET_MIXER"):
            self.assertNotIn(name, src, f"{name}이 조사 코드에서 사용되고 있습니다")
        self.assertIn(C.MSP_SET_RAW_RC, C.DANGEROUS_COMMANDS)
        self.assertTrue(C.DANGEROUS_COMMANDS <= C.WRITE_COMMANDS)


if __name__ == "__main__":
    unittest.main()

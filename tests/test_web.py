import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import d4d_web.api as api  # noqa: E402
import d4d_web.library as library  # noqa: E402
from d4d_web.llm import deterministic_patch  # noqa: E402


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (
            api.ARTIFACTS_DIR,
            api.SCENARIOS_DIR,
            library.ARTIFACTS_DIR,
            library.SCENARIOS_DIR,
        )
        api.ARTIFACTS_DIR = library.ARTIFACTS_DIR = root / "artifacts"
        api.SCENARIOS_DIR = library.SCENARIOS_DIR = root / "scenarios"
        api.SCENARIOS_DIR.mkdir(parents=True)
        self.transport = httpx.ASGITransport(app=api.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        self.old_hardware_service = getattr(api, "hardware_service", None)
        self.old_write_env = os.environ.get("D4D_HARDWARE_WRITES")

    async def asyncTearDown(self):
        await self.client.aclose()
        api.ARTIFACTS_DIR, api.SCENARIOS_DIR, library.ARTIFACTS_DIR, library.SCENARIOS_DIR = self.old
        if self.old_hardware_service is not None:
            api.hardware_service = self.old_hardware_service
        if self.old_write_env is None:
            os.environ.pop("D4D_HARDWARE_WRITES", None)
        else:
            os.environ["D4D_HARDWARE_WRITES"] = self.old_write_env
        self.tmp.cleanup()

    def scenario(self):
        return json.loads((ROOT / "scenarios" / "demo_arena.json").read_text(encoding="utf-8"))

    def install_hardware(self, fc=None, *, factory=None, now=None, ttl=30.0):
        from d4d_web.hardware import HardwareService
        from tests.test_control import _ControlFakeFc

        fc = fc or _ControlFakeFc()
        api.hardware_service = HardwareService(
            client_factory=factory or (lambda: fc),
            now=now,
            token_ttl=ttl,
            backup_dir=Path(self.tmp.name) / "control_backups",
        )
        return fc

    async def test_validate_and_none_llm_patch(self):
        response = await self.client.post("/api/validate", json=self.scenario())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["valid"])

        bad = self.scenario()
        bad["field"]["width_m"] = -1
        response = await self.client.post("/api/validate", json=bad)
        self.assertEqual(response.status_code, 422)

        spider = json.loads((ROOT / "scenarios" / "demo_spider.json").read_text(encoding="utf-8"))
        result = deterministic_patch("노드 3에서 사진 30KB를 회수해", spider)
        self.assertEqual(result["provider"], "none")
        added = result["scenario"]["messages"][-1]
        self.assertEqual(added["bytes"], 30 * 1024)
        self.assertEqual(added["from"], 3)

    async def test_demo_patch_names_shelter_without_changing_node_role(self):
        spider = json.loads((ROOT / "scenarios" / "demo_spider.json").read_text(encoding="utf-8"))
        result = deterministic_patch("노드 3을 대피소로, 사진 30KB 회수 메시지 추가", spider)
        node = next(tag for tag in result["scenario"]["tags"] if tag["id"] == 3)
        self.assertEqual(node["name"], "대피소")
        self.assertEqual(node["role"], "ble")
        self.assertNotIn("name", next(tag for tag in spider["tags"] if tag["id"] == 3))
        self.assertEqual(result["scenario"]["messages"][-1]["bytes"], 30720)
        self.assertEqual(len(result["diff"]), 2)

    async def test_sse_approval_card_drill_and_package(self):
        response = await self.client.post(
            "/api/plan/stream",
            json={"run_id": "web-test", "scenario": self.scenario(), "params": {"mc_runs": 20}},
        )
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("event: trace", body)
        self.assertIn("event: complete", body)
        self.assertLess(body.index('"tool": "analyze"'), body.index('"tool": "verify"'))

        out = library.run_dir("web-test")
        self.assertTrue((out / "mission.json").is_file())
        self.assertTrue((out / "plan_card.png").read_bytes().startswith(b"\x89PNG"))
        self.assertIn("#define MISSION_PLAN_CARD_ID 500", (out / "mission_data.h").read_text(encoding="utf-8"))
        drill = json.loads((out / "drill_timeline.json").read_text(encoding="utf-8"))
        self.assertEqual(drill["events"][1]["kind"], "emp")
        self.assertEqual(len(drill["samples"]), 4)

        denied = await self.client.get("/api/runs/web-test/package")
        self.assertEqual(denied.status_code, 409)
        approved = await self.client.post("/api/runs/web-test/approve", json={"approved_by": "test"})
        self.assertEqual(approved.status_code, 200)
        package = await self.client.get("/api/runs/web-test/package")
        self.assertEqual(package.status_code, 200)
        self.assertEqual(package.headers["content-type"], "application/zip")

        card = await self.client.get("/api/cards/web-test.png")
        self.assertEqual(card.status_code, 200)
        self.assertTrue(card.content.startswith(b"\x89PNG"))
        timeline = await self.client.get("/api/drill/web-test")
        self.assertEqual(timeline.status_code, 200)
        self.assertTrue(timeline.json()["result"]["verified"])

    async def test_release_gate_requires_approval_and_blocks_stale(self):
        scenario = self.scenario()
        (api.SCENARIOS_DIR / "demo_arena.json").write_text(
            json.dumps(scenario, ensure_ascii=False), encoding="utf-8",
        )
        response = await self.client.post(
            "/api/plan/stream",
            json={
                "run_id": "gate-test",
                "scenario_id": "demo_arena",
                "scenario": scenario,
                "params": {"mc_runs": 20},
            },
        )
        self.assertEqual(response.status_code, 200)
        out = library.run_dir("gate-test")
        with zipfile.ZipFile(out / "gate-test.zip", "w") as bundle:
            bundle.writestr("note.txt", "early")
        paths = ("/api/runs/gate-test/package", "/api/cards/gate-test.png", "/api/drill/gate-test")
        for path in paths:
            denied = await self.client.get(path)
            self.assertEqual(denied.status_code, 409, path)

        approved = await self.client.post("/api/runs/gate-test/approve", json={"approved_by": "test"})
        self.assertEqual(approved.status_code, 200)
        for path in paths:
            opened = await self.client.get(path)
            self.assertEqual(opened.status_code, 200, path)

        scenario["name"] = f"{scenario['name']} stale"
        (api.SCENARIOS_DIR / "demo_arena.json").write_text(
            json.dumps(scenario, ensure_ascii=False), encoding="utf-8",
        )
        for path in paths:
            stale = await self.client.get(path)
            self.assertEqual(stale.status_code, 409, path)

    async def test_hardware_profile_and_fc_status_are_sanitized_and_complete(self):
        self.install_hardware()
        profile_response = await self.client.get("/api/hardware/profile")
        self.assertEqual(profile_response.status_code, 200)
        profile = profile_response.json()
        self.assertEqual(profile["fc"]["target"], "MICOAIR743V2")
        self.assertEqual(profile["companion"]["build"], "TAGVISION-20261010-22")
        self.assertEqual(profile["rangefinder_flow"]["model"], "MTF-01P")

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value))
            return set()

        self.assertTrue({"uid", "mac", "address"}.isdisjoint(keys(profile)))

        response = await self.client.get("/api/hardware/fc/status")
        self.assertEqual(response.status_code, 200)
        status = response.json()
        self.assertEqual(status["identity"]["firmware"]["version"], "9.1.0")
        self.assertEqual(status["mask"], 64)
        self.assertEqual(status["rc"]["channels_us"][3], 1000)
        self.assertEqual(status["motors_us"][:4], [1000] * 4)
        self.assertEqual(status["modes"][0]["permanent_id"], 50)
        self.assertIn("sensors", status)
        self.assertIn("gates", status)
        self.assertTrue(status["can_apply_67"])

    async def test_hardware_challenge_requires_flag_confirmation_and_valid_target(self):
        self.install_hardware()
        os.environ.pop("D4D_HARDWARE_WRITES", None)
        disabled = await self.client.post(
            "/api/hardware/fc/override/challenge",
            json={"target": 67, "confirmation": "프로펠러 제거 / 디스아밍"},
        )
        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(disabled.json()["detail"]["code"], "writes_disabled")

        os.environ["D4D_HARDWARE_WRITES"] = "1"
        bad_confirmation = await self.client.post(
            "/api/hardware/fc/override/challenge",
            json={"target": 67, "confirmation": "디스아밍"},
        )
        self.assertEqual(bad_confirmation.status_code, 400)
        self.assertEqual(
            bad_confirmation.json()["detail"]["code"],
            "confirmation_mismatch",
        )
        bad_target = await self.client.post(
            "/api/hardware/fc/override/challenge",
            json={"target": 255, "confirmation": "프로펠러 제거 / 디스아밍"},
        )
        self.assertEqual(bad_target.status_code, 422)

    async def test_hardware_challenge_is_short_lived_single_use_and_target_bound(self):
        clock = [100.0]
        self.install_hardware(now=lambda: clock[0], ttl=5.0)
        os.environ["D4D_HARDWARE_WRITES"] = "1"

        async def challenge():
            return await self.client.post(
                "/api/hardware/fc/override/challenge",
                json={"target": 67, "confirmation": "프로펠러 제거 / 디스아밍"},
            )

        first = await challenge()
        self.assertEqual(first.status_code, 200)
        clock[0] += 6
        expired = await self.client.post(
            "/api/hardware/fc/override/apply",
            json={"target": 67, "token": first.json()["token"]},
        )
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(expired.json()["detail"]["code"], "challenge_expired")

        second = await challenge()
        token = second.json()["token"]
        wrong_target = await self.client.post(
            "/api/hardware/fc/override/apply",
            json={"target": 64, "token": token},
        )
        self.assertEqual(wrong_target.status_code, 409)
        self.assertEqual(wrong_target.json()["detail"]["code"], "challenge_target")

        third = await challenge()
        applied = await self.client.post(
            "/api/hardware/fc/override/apply",
            json={"target": 67, "token": third.json()["token"]},
        )
        self.assertEqual(applied.status_code, 200)
        self.assertTrue(applied.json()["verified"])
        reused = await self.client.post(
            "/api/hardware/fc/override/apply",
            json={"target": 67, "token": third.json()["token"]},
        )
        self.assertEqual(reused.status_code, 409)
        self.assertEqual(reused.json()["detail"]["code"], "challenge_invalid")

    async def test_hardware_apply_rechecks_live_state(self):
        fc = self.install_hardware()
        os.environ["D4D_HARDWARE_WRITES"] = "1"
        challenge = await self.client.post(
            "/api/hardware/fc/override/challenge",
            json={"target": 67, "confirmation": "프로펠러 제거 / 디스아밍"},
        )
        self.assertEqual(challenge.status_code, 200)
        fc.armed = True
        applied = await self.client.post(
            "/api/hardware/fc/override/apply",
            json={"target": 67, "token": challenge.json()["token"]},
        )
        self.assertEqual(applied.status_code, 409)
        self.assertEqual(applied.json()["detail"]["code"], "safety_gate")
        self.assertEqual(fc.mask, 64)

    async def test_hardware_new_masks_are_supported_with_single_use_tokens(self):
        from d4d_fc import codes as C

        os.environ["D4D_HARDWARE_WRITES"] = "1"
        for target in (7, 71):
            with self.subTest(target=target):
                fc = self.install_hardware()
                challenge = await self.client.post(
                    "/api/hardware/fc/override/challenge",
                    json={"target": target, "confirmation": "프로펠러 제거 / 디스아밍"},
                )
                self.assertEqual(challenge.status_code, 200)
                self.assertEqual(challenge.json()["expires_in_s"], 30)
                payload = {"target": target, "token": challenge.json()["token"]}
                applied = await self.client.post("/api/hardware/fc/override/apply", json=payload)
                self.assertEqual(applied.status_code, 200)
                self.assertEqual(applied.json()["after"], target)
                self.assertTrue(applied.json()["verified"])
                self.assertFalse(applied.json()["saved"])
                self.assertNotIn(C.MSP_EEPROM_WRITE, [cmd for cmd, _ in fc.sent])
                status = (await self.client.get("/api/hardware/fc/status")).json()
                self.assertTrue(status["can_apply_override"])
                self.assertTrue(status["can_apply_67"])
                self.assertEqual(status["target_masks"], [7, 67, 71])
                self.assertEqual(status["allowed_masks"], [7, 64, 67, 71])
                self.assertTrue(status["flight_locked"])
                reused = await self.client.post("/api/hardware/fc/override/apply", json=payload)
                self.assertEqual(reused.status_code, 409)
                self.assertEqual(reused.json()["detail"]["code"], "challenge_invalid")

    async def test_hardware_new_masks_preserve_confirmation_expiry_and_target_binding(self):
        os.environ["D4D_HARDWARE_WRITES"] = "1"
        for target in (7, 71):
            with self.subTest(target=target):
                clock = [100.0]
                fc = self.install_hardware(now=lambda: clock[0])
                bad = await self.client.post(
                    "/api/hardware/fc/override/challenge",
                    json={"target": target, "confirmation": "디스아밍"},
                )
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(fc.sent, [])
                payload = {"target": target, "confirmation": "프로펠러 제거 / 디스아밍"}
                challenge = await self.client.post("/api/hardware/fc/override/challenge", json=payload)
                self.assertEqual(challenge.status_code, 200)
                clock[0] += 30.001
                expired = await self.client.post(
                    "/api/hardware/fc/override/apply",
                    json={"target": target, "token": challenge.json()["token"]},
                )
                self.assertEqual(expired.status_code, 409)
                self.assertEqual(expired.json()["detail"]["code"], "challenge_expired")
                challenge = await self.client.post("/api/hardware/fc/override/challenge", json=payload)
                mismatch = await self.client.post(
                    "/api/hardware/fc/override/apply",
                    json={"target": 71 if target == 7 else 7, "token": challenge.json()["token"]},
                )
                self.assertEqual(mismatch.status_code, 409)
                self.assertEqual(mismatch.json()["detail"]["code"], "challenge_target")
                self.assertEqual(fc.mask, 64)

    async def test_hardware_new_masks_require_full_gates_at_challenge_and_apply(self):
        from tests.test_control import _ControlFakeFc

        os.environ["D4D_HARDWARE_WRITES"] = "1"
        for target in (7, 71):
            for field, value in (("armed", True), ("throttle", 1100), ("motors", [1000, 1001, 1000, 1000])):
                with self.subTest(target=target, field=field):
                    fc = self.install_hardware(_ControlFakeFc(**{field: value}))
                    payload = {"target": target, "confirmation": "프로펠러 제거 / 디스아밍"}
                    blocked = await self.client.post("/api/hardware/fc/override/challenge", json=payload)
                    self.assertEqual(blocked.status_code, 409)
                    self.assertEqual(blocked.json()["detail"]["code"], "safety_gate")
                    fc = self.install_hardware()
                    challenge = await self.client.post("/api/hardware/fc/override/challenge", json=payload)
                    self.assertEqual(challenge.status_code, 200)
                    setattr(fc, field, value)
                    applied = await self.client.post(
                        "/api/hardware/fc/override/apply",
                        json={"target": target, "token": challenge.json()["token"]},
                    )
                    self.assertEqual(applied.status_code, 409)
                    self.assertEqual(applied.json()["detail"]["code"], "safety_gate")
                    self.assertEqual(fc.mask, 64)

    async def test_hardware_serial_errors_are_structured(self):
        self.install_hardware(
            factory=mock.Mock(side_effect=OSError("COM9 access denied"))
        )
        response = await self.client.get("/api/hardware/fc/status")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "fc_unavailable")


if __name__ == "__main__":
    unittest.main()

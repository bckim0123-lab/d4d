import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4d_planner.agent import run_agent  # noqa: E402
from d4d_planner.analysis import analyze  # noqa: E402
from d4d_planner.drift import max_lw, sigma  # noqa: E402
from d4d_planner.export import write_all  # noqa: E402
from d4d_planner.grid import Grid, edt_cells  # noqa: E402
from d4d_planner.model import DroneSpec, Message, PlanParams, Shape  # noqa: E402
from d4d_planner.network import comm_stop_order, messages_at_stop  # noqa: E402
from d4d_planner.scenario import ScenarioError, load_scenario, parse_scenario  # noqa: E402
from d4d_planner.sim import p_link  # noqa: E402

SC = ROOT / "scenarios"


def minimal(**over):
    d = {
        "name": "t",
        "field": {"width_m": 10, "height_m": 10, "resolution_m": 0.25},
        "start": {"x": 1, "y": 1},
        "goal": {"x": 9, "y": 9, "tolerance_m": 1.0},
        "tags": [],
        "zones": [],
    }
    d.update(over)
    return d


class ShapeTests(unittest.TestCase):
    def test_contains(self):
        self.assertTrue(Shape("rect", (0, 0, 2, 2)).contains(1, 1))
        self.assertFalse(Shape("rect", (0, 0, 2, 2)).contains(3, 1))
        self.assertTrue(Shape("circle", (0, 0, 1)).contains(0.5, 0.5))
        self.assertFalse(Shape("circle", (0, 0, 1)).contains(1, 1))
        tri = Shape("polygon", ((0, 0), (4, 0), (0, 4)))
        self.assertTrue(tri.contains(1, 1))
        self.assertFalse(tri.contains(3, 3))


class EdtTests(unittest.TestCase):
    def test_matches_bruteforce(self):
        rng = random.Random(1)
        nx, ny = 13, 9
        mask = [1 if rng.random() < 0.1 else 0 for _ in range(nx * ny)]
        mask[5] = 1
        got = edt_cells(mask, nx, ny)
        pts = [(i % nx, i // nx) for i, m in enumerate(mask) if m]
        for idx in range(nx * ny):
            x, y = idx % nx, idx // nx
            want = min(math.hypot(x - a, y - b) for a, b in pts)
            self.assertAlmostEqual(got[idx], want, places=6)


class DriftTests(unittest.TestCase):
    def test_monotonic_and_inverse(self):
        d = DroneSpec()
        self.assertLess(sigma(0.05, 1, d), sigma(0.05, 5, d))
        lw = max_lw(0.05, 0.4, d)
        self.assertAlmostEqual(sigma(0.05, lw, d), 0.4, places=6)
        self.assertEqual(max_lw(0.5, 0.4, d), 0.0)


class ScenarioTests(unittest.TestCase):
    def test_validation_errors(self):
        with self.assertRaises(ScenarioError):
            parse_scenario(minimal(start={"x": 99, "y": 1}))
        with self.assertRaises(ScenarioError):
            parse_scenario(minimal(link_policy="whatever"))
        with self.assertRaises(ScenarioError):
            parse_scenario(minimal(tags=[{"id": 1, "x": 1, "y": 1}, {"id": 1, "x": 2, "y": 2}]))
        with self.assertRaises(ScenarioError):
            parse_scenario(minimal(zones=[{"kind": "jam", "shape": "circle", "x": 1, "y": 1, "r": 1, "value": 2}]))
        with self.assertRaises(ScenarioError):
            parse_scenario(minimal(drone={"bogus": 1}))

    def test_demo_files_load(self):
        for f in SC.glob("*.json"):
            load_scenario(f)


class AgentTests(unittest.TestCase):
    def test_demo_arena_passes_without_suggestions(self):
        res = run_agent(load_scenario(SC / "demo_arena.json"))
        self.assertTrue(res.success)
        self.assertEqual(res.suggested_tags, [])
        wps = res.plan.waypoints
        self.assertEqual(wps[0].kind, "takeoff")
        self.assertEqual(wps[-1].kind, "land")
        # abort 정책: 어떤 웨이포인트도 재밍 구역 안에 있으면 안 된다
        self.assertTrue(all(w.link_expected for w in wps))
        self.assertEqual(res.plan.jam_len_m, 0.0)
        # 각 fix 도착 오차가 태그 인식 반경 안(z*sigma <= radius)
        d = res.scenario.drone
        for w in wps:
            if w.kind == "fix":
                self.assertLessEqual(res.params.z * w.expected_sigma_m, d.tag_detect_radius_m + 1e-6)

    def test_waypoints_are_collision_free(self):
        res = run_agent(load_scenario(SC / "demo_arena.json"))
        g = res.grid
        for a, b in zip(res.plan.waypoints, res.plan.waypoints[1:]):
            self.assertTrue(g.line_clear((a.x, a.y), (b.x, b.y)), f"{a.idx}->{b.idx}")

    def test_jam_scenario_suggests_tags_and_crosses_jam(self):
        res = run_agent(load_scenario(SC / "demo_jam.json"))
        self.assertTrue(res.success)
        self.assertGreater(len(res.suggested_tags), 0)
        self.assertGreater(res.plan.jam_len_m, 0.0)
        self.assertTrue(any(not w.link_expected for w in res.plan.waypoints))

    def test_no_suggestions_flag_reports_failure(self):
        from d4d_planner.agent import AgentConfig

        res = run_agent(load_scenario(SC / "demo_jam.json"), cfg=AgentConfig(suggest_tags=False))
        self.assertFalse(res.success)
        self.assertTrue(res.issues)

    def test_abort_policy_with_mandatory_jam_is_reported(self):
        sc = load_scenario(SC / "demo_jam.json")
        sc.link_policy = "abort"
        res = run_agent(sc)
        self.assertFalse(res.success)
        self.assertTrue(res.analysis.needs_jam_crossing)
        self.assertTrue(res.analysis.blocking)

    def test_unreachable_goal(self):
        data = minimal(zones=[{"kind": "obstacle", "shape": "rect", "x": 0, "y": 5, "w": 10, "h": 1}])
        res = run_agent(parse_scenario(data))
        self.assertFalse(res.success)
        self.assertTrue(res.analysis.blocking)

    def test_open_field_without_tags_gets_tags(self):
        res = run_agent(parse_scenario(minimal()))
        self.assertTrue(res.success)
        self.assertGreater(len(res.suggested_tags), 0)

    def test_deterministic(self):
        a = run_agent(load_scenario(SC / "demo_arena.json"))
        b = run_agent(load_scenario(SC / "demo_arena.json"))
        self.assertEqual([(w.x, w.y) for w in a.plan.waypoints], [(w.x, w.y) for w in b.plan.waypoints])
        self.assertEqual(a.mc.counts, b.mc.counts)

    def test_trace_callback_streams_same_steps(self):
        streamed = []
        res = run_agent(load_scenario(SC / "demo_arena.json"), on_step=streamed.append)
        self.assertEqual(streamed, res.trace)
        self.assertEqual(streamed[0].tool, "analyze")
        self.assertEqual(streamed[-1].tool, "verify")

    def test_time_budget_violation_fails(self):
        sc = load_scenario(SC / "demo_arena.json")
        sc.drone.endurance_s = 60
        res = run_agent(sc)
        self.assertFalse(res.success)
        self.assertTrue(any("배터리" in s for s in res.issues))


def spider(**over):
    """재밍 구역 안 BLE 노드 하나에서 데이터를 회수해 착륙지로 가져오는 최소 네트워크 시나리오."""
    d = minimal(
        field={"width_m": 20, "height_m": 12, "resolution_m": 0.25},
        start={"x": 2, "y": 2},
        goal={"x": 2, "y": 2, "tolerance_m": 1.0},
        tags=[
            {"id": 1, "x": 6, "y": 5},
            {"id": 2, "x": 10, "y": 8, "role": "ble"},
            {"id": 3, "x": 15, "y": 4, "role": "mailbox"},
        ],
        messages=[{"name": "log", "from": 2, "to": "goal", "bytes": 1024}],
        zones=[{"kind": "jam", "shape": "circle", "x": 10, "y": 8, "r": 2.5, "value": 1.0}],
        link_policy="continue_mission",
    )
    d.update(over)
    return d


class NetworkModelTests(unittest.TestCase):
    def test_legacy_scenarios_have_no_network(self):
        sc = load_scenario(SC / "demo_arena.json")
        self.assertEqual(sc.messages, [])
        self.assertEqual(sc.nodes, [])
        self.assertTrue(all(t.role == "marker" for t in sc.tags))
        self.assertFalse(any(o.action == "comm" for o in sc.objectives))
        res = run_agent(sc)
        self.assertEqual(res.mc.delivery_rate, 1.0)
        self.assertFalse(any(w.kind in ("comm", "read") for w in res.plan.waypoints))

    def test_message_validation(self):
        with self.assertRaises(ScenarioError):  # marker 태그에는 메시지를 둘 수 없다
            parse_scenario(spider(messages=[{"name": "a", "from": 1, "to": "goal"}]))
        with self.assertRaises(ScenarioError):  # 없는 노드
            parse_scenario(spider(messages=[{"name": "a", "from": 99, "to": "goal"}]))
        with self.assertRaises(ScenarioError):  # 출발=도착
            parse_scenario(spider(messages=[{"name": "a", "from": 2, "to": 2}]))
        with self.assertRaises(ScenarioError):  # 지상국 -> 지상국
            parse_scenario(spider(messages=[{"name": "a", "from": "start", "to": "goal"}]))
        with self.assertRaises(ScenarioError):  # 알 수 없는 역할
            parse_scenario(spider(tags=[{"id": 1, "x": 1, "y": 1, "role": "laser"}], messages=[]))
        sc = parse_scenario(spider())
        self.assertEqual([o.action for o in sc.objectives], ["comm", "land"])
        self.assertEqual(sc.objectives[0].node_id, 2)

    def test_comm_stop_order_respects_src_before_dst(self):
        msgs = [Message("a", 2, "goal"), Message("b", "start", 4), Message("c", 4, 3)]
        self.assertEqual(comm_stop_order(msgs), [2, 4, 3])
        # 왕복: A->B 와 B->A 는 A를 다시 방문해야 한다
        self.assertEqual(comm_stop_order([Message("x", 1, 2), Message("y", 2, 1)]), [1, 2, 1])
        # 이미 뒤에 있는 목적지는 다시 붙이지 않는다
        self.assertEqual(comm_stop_order([Message("x", 1, 2), Message("y", 1, 2)]), [1, 2])
        # 우선순위가 순서를 바꾼다
        self.assertEqual(comm_stop_order([Message("x", 1, "goal", priority=2), Message("y", 2, "goal", priority=1)]), [2, 1])

    def test_messages_at_stop(self):
        sc = parse_scenario(
            spider(messages=[
                {"name": "a", "from": 2, "to": 3},
                {"name": "b", "from": 3, "to": 2},
            ])
        )
        self.assertEqual([o.node_id for o in sc.objectives[:-1]], [2, 3, 2])
        self.assertEqual([[m.name for m in x] for x in messages_at_stop(sc, 0)], [["a"], []])
        self.assertEqual([[m.name for m in x] for x in messages_at_stop(sc, 1)], [["b"], ["a"]])
        self.assertEqual([[m.name for m in x] for x in messages_at_stop(sc, 2)], [[], ["b"]])

    def test_p_link_shape(self):
        d = DroneSpec()
        self.assertAlmostEqual(p_link(0.0, 0.0, d), d.ble_link_prob)
        self.assertGreater(p_link(0.5, 0.0, d), p_link(1.5, 0.0, d))  # 거리에 따라 감소
        self.assertGreater(p_link(0.6, 0.0, d), p_link(0.6, 1.0, d))  # 재밍에 따라 감소
        self.assertEqual(p_link(d.ble_range_m, 0.0, d), 0.0)  # 범위 밖
        self.assertEqual(p_link(d.ble_range_m * (1 - d.ble_jam_shrink), 1.0, d), 0.0)
        self.assertGreater(p_link(0.3, 0.0, d, range_m=5.0), p_link(0.3, 0.0, d, range_m=1.0))

    def test_spider_mission_has_comm_waypoints_and_delivers(self):
        res = run_agent(parse_scenario(spider()))
        self.assertTrue(res.success, res.issues)
        kinds = [w.kind for w in res.plan.waypoints]
        self.assertTrue(any(k in ("comm", "read") for k in kinds))
        stop = next(w for w in res.plan.waypoints if w.kind in ("comm", "read"))
        self.assertEqual(stop.tag_id, 2)
        self.assertGreater(stop.xfer_bytes, 0)
        self.assertGreater(stop.dwell_s, res.scenario.drone.comm_dwell_s)
        self.assertGreaterEqual(res.mc.delivery_by_msg["log"], res.params.min_delivery)
        self.assertIn(2, res.plan.tags_used)
        # 교신 지점은 노드 자신을 경유 fix 로 중복 사용하지 않는다
        idx = res.plan.waypoints.index(stop)
        self.assertFalse(res.plan.waypoints[idx - 1].kind == "fix" and res.plan.waypoints[idx - 1].tag_id == 2)

    def test_agent_switches_jammed_ble_node_to_mailbox(self):
        # 재밍 1.0 + 강한 축소율: BLE 유효 범위가 교신 고도보다 짧아져 교신이 거의 안 된다
        res = run_agent(parse_scenario(spider(drone={"ble_jam_shrink": 0.9})))
        self.assertTrue(res.success, res.issues)
        self.assertTrue(any(s.tool == "revise_node" for s in res.trace))
        node = res.scenario.tag(2)
        self.assertEqual(node.role, "mailbox")
        self.assertTrue(node.role_suggested)
        self.assertEqual([t.id for t in res.suggested_roles], [2])

    def test_unfixable_delivery_is_reported(self):
        # 광학 메일박스인데 판독률이 낮으면 역할 전환으로도 못 고친다 -> 이슈로 보고
        data = spider(
            tags=[{"id": 1, "x": 6, "y": 5}, {"id": 2, "x": 10, "y": 8, "role": "mailbox"}],
            drone={"read_code_prob": 0.2},
        )
        res = run_agent(parse_scenario(data))
        self.assertFalse(res.success)
        self.assertTrue(any("판독" in s for s in res.issues))
        self.assertFalse(any(s.tool == "revise_node" for s in res.trace))

    def test_demo_spider_outputs(self):
        res = run_agent(load_scenario(SC / "demo_spider.json"))
        self.assertTrue(res.success, res.issues)
        self.assertEqual(set(res.mc.delivery_by_msg), {"관측센서_로그", "미션갱신_명령", "노드4_상태보고"})
        with tempfile.TemporaryDirectory() as tmp:
            write_all(res, Path(tmp))
            m = json.loads((Path(tmp) / "mission.json").read_text(encoding="utf-8"))
            self.assertEqual(len(m["network"]["nodes"]), 3)
            self.assertEqual(len(m["network"]["messages"]), 3)
            comm_wps = [w for w in m["waypoints"] if w["kind"] in ("comm", "read")]
            self.assertEqual(len(comm_wps), 3)
            self.assertTrue(all("comm" in w for w in comm_wps))
            self.assertEqual(comm_wps[0]["comm"]["pickup"], ["관측센서_로그"])
            h = (Path(tmp) / "mission_data.h").read_text(encoding="utf-8")
            self.assertIn("WP_COMM = 5", h)
            self.assertIn("#define MISSION_MSG_COUNT 3", h)
            self.assertIn("{ 2, -2, 4096, 1 }", h)
            r = (Path(tmp) / "report.md").read_text(encoding="utf-8")
            self.assertIn("3-1. 네트워크", r)
            svg = (Path(tmp) / "plan.svg").read_text(encoding="utf-8")
            self.assertIn("광학 메일박스 노드", svg)


class AnalysisTests(unittest.TestCase):
    def test_camera_fov_warning(self):
        sc = parse_scenario(minimal(drone={"tag_detect_radius_m": 0.5}))
        p = PlanParams()
        self.assertFalse(any("화각" in w for w in analyze(sc, Grid(sc, p), p).warnings))
        sc = parse_scenario(minimal(drone={"tag_detect_radius_m": 1.0}))
        self.assertTrue(any("화각" in w for w in analyze(sc, Grid(sc, p), p).warnings))

    def test_inflation_blocks_near_wall(self):
        sc = parse_scenario(minimal(start={"x": 0.3, "y": 5}))
        p = PlanParams()
        a = analyze(sc, Grid(sc, p), p)
        self.assertTrue(a.blocking)  # 필드 경계에 너무 가까움


class ExportTests(unittest.TestCase):
    def test_outputs(self):
        res = run_agent(load_scenario(SC / "demo_jam.json"))
        with tempfile.TemporaryDirectory() as tmp:
            files = write_all(res, Path(tmp))
            names = {f.name for f in files}
            self.assertEqual(names, {"agent_trace.json", "mission.json", "mission_data.h", "plan.svg", "report.md"})
            m = json.loads((Path(tmp) / "mission.json").read_text(encoding="utf-8"))
            self.assertEqual(len(m["waypoints"]), len(res.plan.waypoints))
            self.assertTrue(any(t["install_required"] for t in m["tags"]))
            h = (Path(tmp) / "mission_data.h").read_text(encoding="utf-8")
            self.assertIn(f"#define MISSION_WP_COUNT {len(res.plan.waypoints)}", h)
            self.assertIn("MISSION_LINK_POLICY_CONTINUE 1", h)

    def test_failed_plan_still_writes_report(self):
        sc = load_scenario(SC / "demo_jam.json")
        sc.link_policy = "abort"
        res = run_agent(sc)
        with tempfile.TemporaryDirectory() as tmp:
            files = write_all(res, Path(tmp))
            self.assertNotIn("mission.json", {f.name for f in files})
            self.assertIn("report.md", {f.name for f in files})


if __name__ == "__main__":
    unittest.main()

"""통신 두절(EMP/재밍) 비행 몬테카를로 시뮬레이터.

드론은 '믿는 위치(believed)'로 웨이포인트를 추종하고, 실제 위치(true)는
광류 스케일 오차/요 드리프트/랜덤워크로 어긋난다. fix 웨이포인트에서 태그가 인식되면
실제 위치 기준으로 믿는 위치가 리셋된다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .grid import Grid
from .model import DroneSpec, MissionPlan, PlanParams, Scenario, Tag
from .network import messages_at_stop

STEP_M = 0.1
COMM_TRIES = 3  # 교신 재시도 횟수 (태그 인식과 동일)


def p_link(dist_m: float, jam: float, d: DroneSpec, range_m: float | None = None) -> float:
    """근접 BLE 1회 시도 성공 확률.

    dist_m: 드론 안테나 - 노드 사이 직선 거리(교신 고도 포함), jam: 노드 위치의 재밍 강도(0~1).
    재밍이 세질수록 유효 범위가 (1 - ble_jam_shrink * jam)배로 줄어들고, 범위 안에서는 거리 제곱으로 떨어진다.
    경로 손실 ∝ d² 라는 링크 버짓 추정에서 나온 **가정 모델**이며 실측으로 보정해야 한다.
    """
    r0 = range_m if range_m is not None and range_m > 0 else d.ble_range_m
    r_eff = r0 * max(0.05, 1.0 - d.ble_jam_shrink * max(0.0, min(1.0, jam)))
    if dist_m >= r_eff:
        return 0.0
    return d.ble_link_prob * (1.0 - (dist_m / r_eff) ** 2)


def p_read(d: DroneSpec) -> float:
    """디스플레이 코드(mailbox) 1회 판독 성공 확률. 광학이라 재밍과 무관하다."""
    return d.read_code_prob


@dataclass
class MCResult:
    runs: int
    counts: dict[str, int]
    success_rate: float
    collision_rate: float
    mean_time_s: float
    p95_time_s: float
    worst_final_err_m: float
    traces: list[list[tuple[float, float]]] = field(default_factory=list)
    fix_miss_by_tag: dict[int, int] = field(default_factory=dict)
    # 네트워크: 메시지별 전달률, 노드별 교신 실패 횟수
    delivery_by_msg: dict[str, float] = field(default_factory=dict)
    comm_fail_by_node: dict[int, int] = field(default_factory=dict)

    @property
    def delivery_rate(self) -> float:
        """메시지 중 가장 낮은 전달률. 메시지가 없으면 1.0."""
        return min(self.delivery_by_msg.values()) if self.delivery_by_msg else 1.0

    def passes(self, params: PlanParams) -> bool:
        return (
            self.success_rate >= params.min_success
            and self.collision_rate <= params.max_collision
            and self.delivery_rate >= params.min_delivery
        )

    def dominant_failure(self) -> str:
        fails = {k: v for k, v in self.counts.items() if k != "ok"}
        return max(fails, key=fails.get) if fails else ""

    def worst_node(self) -> int | None:
        if not self.comm_fail_by_node:
            return None
        return max(self.comm_fail_by_node, key=self.comm_fail_by_node.get)


def _exchange_ok(rng: random.Random, node: Tag, slant_m: float, jam: float, d: DroneSpec) -> bool:
    if node.role == "ble":
        p = p_link(slant_m, jam, d, d.node_range_m(node))
    else:
        p = p_read(d)
    return any(rng.random() < p for _ in range(COMM_TRIES))


def simulate(plan: MissionPlan, sc: Scenario, grid: Grid, params: PlanParams, n: int | None = None, keep_traces: int = 25) -> MCResult:
    n = n or params.mc_runs
    rng = random.Random(params.seed)
    d = sc.drone
    wps = plan.waypoints
    tags = {t.id: t for t in sc.tags}
    abort_policy = sc.link_policy == "abort"
    counts: dict[str, int] = {}
    times: list[float] = []
    traces = []
    worst_final = 0.0
    miss_by_tag: dict[int, int] = {}
    comm_fail: dict[int, int] = {}
    delivered: dict[str, int] = {m.name: 0 for m in sc.messages}
    # 구간별로 싣고 내려놓을 메시지를 미리 계산 (결정론적)
    stop_plan = {k: messages_at_stop(sc, k) for k, o in enumerate(sc.objectives) if o.action == "comm"}
    to_goal = [m.name for m in sc.messages if m.dst == "goal"]

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for run in range(n):
        es = rng.gauss(0.0, d.flow_scale_sigma)
        eh = rng.gauss(0.0, d.yaw_drift_sigma)
        bx, by = wps[0].x, wps[0].y
        tx, ty = bx, by
        t_s = 0.0
        status = "ok"
        trace = [(tx, ty)] if run < keep_traces else None
        step_i = 0
        carried = {m.name for m in sc.messages if m.src == "start"}
        done: set[str] = set()
        for w in wps[1:]:
            dist = math.hypot(w.x - bx, w.y - by)
            steps = max(1, int(math.ceil(dist / STEP_M)))
            ux, uy = (w.x - bx) / steps, (w.y - by) / steps
            ds = dist / steps
            for _ in range(steps):
                if not grid.inside(tx, ty):
                    status = "collision"
                    break
                idx = grid.cell(tx, ty)
                m = grid.flow[idx]
                ang = eh * m
                ca, sa = math.cos(ang), math.sin(ang)
                k = 1.0 + es * m
                sx, sy = (ux * ca - uy * sa) * k, (ux * sa + uy * ca) * k
                q = d.noise_per_sqrt_m * math.sqrt(ds) * math.sqrt(m)
                tx += sx + rng.gauss(0.0, q)
                ty += sy + rng.gauss(0.0, q)
                t_s += ds / w.speed
                step_i += 1
                if trace is not None and step_i % 5 == 0:
                    trace.append((tx, ty))
                if not grid.inside(tx, ty) or grid.collide[grid.cell(tx, ty)]:
                    status = "collision"
                    break
                if abort_policy and grid.jam[grid.cell(tx, ty)] > 0:
                    status = "link_abort"
                    break
            if status != "ok":
                break
            bx, by = w.x, w.y
            if w.kind in ("fix", "comm", "read"):
                tag = tags[w.tag_id]
                dd = math.hypot(tx - tag.x, ty - tag.y)
                seen = dd <= d.tag_detect_radius_m and any(rng.random() < d.tag_detect_prob for _ in range(3))
                t_s += d.tag_dwell_s if w.kind == "fix" else w.dwell_s
                if not seen:
                    status = "tag_miss"
                    miss_by_tag[w.tag_id] = miss_by_tag.get(w.tag_id, 0) + 1
                    break
                bx = tx + rng.gauss(0.0, d.tag_sigma_m)
                by = ty + rng.gauss(0.0, d.tag_sigma_m)
                if w.kind in ("comm", "read"):
                    # 태그 위에서 교신. 실패해도 비행은 계속하고 전달률에만 반영한다.
                    slant = math.hypot(dd, d.comm_altitude_m)
                    jam = grid.jam[grid.cell(tag.x, tag.y)]
                    if _exchange_ok(rng, tag, slant, jam, d):
                        pick, drop = stop_plan.get(w.segment, ([], []))
                        for m in pick:
                            carried.add(m.name)
                        for m in drop:
                            if m.name in carried:
                                done.add(m.name)
                    else:
                        comm_fail[w.tag_id] = comm_fail.get(w.tag_id, 0) + 1
            elif w.kind in ("hover", "land"):
                t_s += w.dwell_s
                obj = sc.objectives[w.segment]
                err = math.hypot(tx - obj.x, ty - obj.y)
                if w.kind == "land":
                    worst_final = max(worst_final, err)
                if err > obj.tolerance_m:
                    status = "goal_miss"
                    break
        if trace is not None:
            trace.append((tx, ty))
            traces.append(trace)
        bump(status)
        if status == "ok":
            times.append(t_s)
            for name in to_goal:
                if name in carried:
                    done.add(name)
        for name in done:
            delivered[name] += 1

    times.sort()
    ok = counts.get("ok", 0)
    return MCResult(
        runs=n,
        counts=counts,
        success_rate=ok / n,
        collision_rate=counts.get("collision", 0) / n,
        mean_time_s=sum(times) / len(times) if times else 0.0,
        p95_time_s=times[min(len(times) - 1, int(0.95 * len(times)))] if times else 0.0,
        worst_final_err_m=worst_final,
        traces=traces,
        fix_miss_by_tag=miss_by_tag,
        delivery_by_msg={k: v / n for k, v in delivered.items()},
        comm_fail_by_node=comm_fail,
    )

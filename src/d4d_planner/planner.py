"""드리프트 인지 경로 계획.

구조
  1. 각 위치 보정 지점(출발점/태그)에서 격자 Dijkstra 확장 (가중 이동거리 상한까지)
  2. 확장 결과로 '지점 -> 지점' 후보 레그를 만들고, 직선화 + 오차예산/이격 검증
  3. 검증을 통과한 레그만으로 그래프를 구성해 최소 시간 경로(태그 경유 순서) 선택
  4. 목표를 순서대로 이어 붙여 웨이포인트 생성
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Optional

from .drift import max_lw, sigma
from .grid import NEIGH, Grid
from .model import (
    Leg,
    MissionPlan,
    Objective,
    PlanParams,
    Scenario,
    Tag,
    Waypoint,
)
from .network import comm_time_s, stop_xfer_bytes

INF = float("inf")
INTERMEDIATE_TOL_FRAC = 0.5  # 중간 목표: z*sigma <= 0.5 * 태그 인식 반경
TAKEOFF_S = 4.0
LAND_S = 3.0


class Expansion:
    __slots__ = ("dist", "lw", "par", "src")

    def __init__(self, src: int):
        self.dist: dict[int, float] = {src: 0.0}
        self.lw: dict[int, float] = {src: 0.0}
        self.par: dict[int, int] = {src: -1}
        self.src = src


@dataclass
class Rejection:
    src: str
    dst: str
    reason: str  # drift | clearance | unreached
    e_end: float = 0.0
    where: Optional[tuple[float, float]] = None


@dataclass
class SegmentResult:
    ok: bool
    legs: list[Leg] = field(default_factory=list)
    reason: str = ""
    rejections: list[Rejection] = field(default_factory=list)


class PlanContext:
    def __init__(self, sc: Scenario, grid: Grid, params: PlanParams):
        self.sc = sc
        self.grid = grid
        self.params = params
        self.drone = sc.drone
        self.tags: list[Tag] = list(sc.tags)
        self.usable: list[Tag] = [
            t for t in self.tags if grid.inside(t.x, t.y) and not grid.blocked[grid.cell(t.x, t.y)]
        ]
        self.blocked_tags: list[Tag] = [t for t in self.tags if t not in self.usable]
        self.tol_max = max([self.drone.tag_detect_radius_m] + [self.arrival_tol(k) for k in range(len(sc.objectives))])
        self.lw_tag = max_lw(self.drone.tag_sigma_m, self.tol_max / params.z, self.drone)
        self._tag_exp: dict[int, Expansion] = {}

    # -------------------------------------------------------------- Dijkstra 확장
    def expand(self, xy: tuple[float, float], lw_max: float) -> Expansion:
        g = self.grid
        src = g.cell(*xy)
        ex = Expansion(src)
        dist, lw, par = ex.dist, ex.lw, ex.par
        heap = [(0.0, src)]
        nx, ny, res = g.nx, g.ny, g.res
        blocked, cost, flow = g.blocked, g.cell_cost, g.flow
        while heap:
            d, c = heapq.heappop(heap)
            if d > dist[c]:
                continue
            if lw[c] >= lw_max:
                continue
            ci, cj = c % nx, c // nx
            for di, dj, step in NEIGH:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < nx and 0 <= nj < ny):
                    continue
                n = nj * nx + ni
                if blocked[n]:
                    continue
                if di and dj and (blocked[cj * nx + ni] or blocked[nj * nx + ci]):
                    continue
                ds = step * res
                nd = d + ds * cost[n]
                if nd < dist.get(n, INF):
                    dist[n] = nd
                    lw[n] = lw[c] + ds * flow[n]
                    par[n] = c
                    heapq.heappush(heap, (nd, n))
        return ex

    def tag_expansion(self, t: Tag) -> Expansion:
        if t.id not in self._tag_exp:
            self._tag_exp[t.id] = self.expand((t.x, t.y), self.lw_tag)
        return self._tag_exp[t.id]

    # -------------------------------------------------------------- 레그 생성
    def _dense_path(self, ex: Expansion, tgt_cell: int, src_xy, tgt_xy):
        cells = []
        c = tgt_cell
        while c != -1:
            cells.append(c)
            c = ex.par[c]
        cells.reverse()
        g = self.grid
        pts = [src_xy] + [g.xy(c) for c in cells[1:-1]] + [tgt_xy]
        cum = [0.0] + [ex.dist[c] for c in cells[1:-1]] + [ex.dist[cells[-1]]]
        if len(cells) == 1:
            pts = [src_xy, tgt_xy]
            cum = [0.0, 0.0]
        return pts, cum

    def _smooth(self, pts, cum):
        g = self.grid
        n = len(pts) - 1
        out = [pts[0]]
        i = 0
        while i < n:
            best = i + 1
            for j in range(min(n, i + 160), i + 1, -1):
                if g.line_clear(pts[i], pts[j]) and g.line_cost(pts[i], pts[j]) <= 1.03 * (cum[j] - cum[i]) + g.res:
                    best = j
                    break
            out.append(pts[best])
            i = best
        return out

    def eval_polyline(self, pts, e0: float):
        """경로를 0.1m 간격으로 샘플링해 오차 진행/이격 여유/구역 통과 길이를 계산."""
        g, d, z = self.grid, self.drone, self.params.z
        lw = 0.0
        length = 0.0
        jam_len = 0.0
        flow_len = 0.0
        t_s = 0.0
        min_margin = INF
        viol = None
        vertex_sigma = [sigma(e0, 0.0, d)]
        e = vertex_sigma[0]
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(math.ceil(L / 0.1)))
            ds = L / n
            slow = False
            for k in range(1, n + 1):
                t = k / n
                idx = g.cell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                m = g.flow[idx]
                lw += ds * m
                e = sigma(e0, lw, d)
                margin = g.dist_obs[idx] - d.radius_m - z * e
                if margin < min_margin:
                    min_margin = margin
                if margin < 0 and viol is None:
                    viol = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                if g.jam[idx] > 0:
                    jam_len += ds
                if m > 1.0:
                    flow_len += ds
                    slow = True
            speed = d.cruise_speed_mps * (d.low_flow_speed_factor if slow else 1.0)
            t_s += L / speed
            length += L
            vertex_sigma.append(e)
        return {
            "lw": lw,
            "length": length,
            "e_end": e,
            "jam_len": jam_len,
            "flow_len": flow_len,
            "time": t_s,
            "min_margin": min_margin,
            "viol": viol,
            "vertex_sigma": vertex_sigma,
        }

    def build_leg(self, ex: Expansion, src_xy, e0, tgt_xy, tol, end_kind, tag_id, name_s, name_t):
        g = self.grid
        tc = g.cell(*tgt_xy)
        if tc not in ex.dist:
            return None, Rejection(name_s, name_t, "unreached")
        pts, cum = self._dense_path(ex, tc, src_xy, tgt_xy)
        pts = self._smooth(pts, cum)
        ev = self.eval_polyline(pts, e0)
        z = self.params.z
        if ev["viol"] is not None:
            return None, Rejection(name_s, name_t, "clearance", ev["e_end"], ev["viol"])
        if z * ev["e_end"] > tol:
            return None, Rejection(name_s, name_t, "drift", ev["e_end"])
        pen_time = sum(g.line_cost(a, b) for a, b in zip(pts, pts[1:])) / self.drone.cruise_speed_mps
        dwell = self.drone.tag_dwell_s if end_kind == "fix" else 0.0
        leg = Leg(
            pts=pts,
            e0=e0,
            e_end=ev["e_end"],
            vertex_sigma=ev["vertex_sigma"],
            end_kind=end_kind,
            tag_id=tag_id,
            lw_m=ev["lw"],
            length_m=ev["length"],
            jam_len_m=ev["jam_len"],
            flow_len_m=ev["flow_len"],
            time_s=ev["time"] + dwell,
            min_clear_margin_m=ev["min_margin"],
        )
        leg._cost = pen_time + dwell  # type: ignore[attr-defined]
        return leg, None

    # -------------------------------------------------------------- 구간 계획
    def arrival_tol(self, k: int) -> float:
        """목표 도착 시 허용 (z*sigma) 한계. 중간 목표는 다음 구간을 시작할 수 있도록 더 엄격하다."""
        obj = self.sc.objectives[k]
        if k == len(self.sc.objectives) - 1:
            return obj.tolerance_m
        return min(obj.tolerance_m, INTERMEDIATE_TOL_FRAC * self.drone.tag_detect_radius_m)

    def plan_segment(self, a_xy, e0: float, obj: Objective, tol: float) -> SegmentResult:
        g, d, z = self.grid, self.drone, self.params.z
        dst_xy = (obj.x, obj.y)
        rej: list[Rejection] = []
        if g.is_blocked_xy(*a_xy) or g.is_blocked_xy(*dst_xy):
            return SegmentResult(False, [], "endpoint_blocked", rej)

        # 교신 목적지인 노드 태그는 이 구간의 경유 태그에서 뺀다(목적지 자체가 보정 지점이므로 fix 중복 방지)
        usable = [t for t in self.usable if obj.action != "comm" or t.id != obj.node_id]
        src_ex = self.expand(a_xy, max_lw(e0, self.tol_max / z, d))
        sources = [("src", a_xy, e0, src_ex)]
        for t in usable:
            sources.append((f"tag{t.id}", (t.x, t.y), d.tag_sigma_m, self.tag_expansion(t)))

        edges: dict[str, list] = {}
        for name, sxy, se0, ex in sources:
            lst = []
            for t in usable:
                if f"tag{t.id}" == name:
                    continue
                leg, r = self.build_leg(ex, sxy, se0, (t.x, t.y), d.tag_detect_radius_m, "fix", t.id, name, f"tag{t.id}")
                if leg:
                    lst.append((f"tag{t.id}", leg))
                elif r.reason != "unreached":
                    rej.append(r)
            leg, r = self.build_leg(ex, sxy, se0, dst_xy, tol, "dest", None, name, "dst")
            if leg:
                lst.append(("dst", leg))
            elif r.reason != "unreached":
                rej.append(r)
            edges[name] = lst

        # 그래프 Dijkstra
        best = {"src": 0.0}
        prev: dict[str, tuple[str, Leg]] = {}
        heap = [(0.0, "src")]
        while heap:
            c, n = heapq.heappop(heap)
            if c > best.get(n, INF):
                continue
            if n == "dst":
                break
            for m, leg in edges.get(n, []):
                nc = c + leg._cost  # type: ignore[attr-defined]
                if nc < best.get(m, INF):
                    best[m] = nc
                    prev[m] = (n, leg)
                    heapq.heappush(heap, (nc, m))
        if "dst" not in prev:
            seen = g.flood(g.cell(*a_xy))
            reason = "no_path" if not seen[g.cell(*dst_xy)] else "drift"
            if reason == "drift" and rej and all(r.reason == "clearance" for r in rej if r.dst == "dst"):
                if any(r.reason == "clearance" for r in rej):
                    reason = "clearance"
            return SegmentResult(False, [], reason, rej)
        legs: list[Leg] = []
        n = "dst"
        while n != "src":
            p, leg = prev[n]
            legs.append(leg)
            n = p
        legs.reverse()
        return SegmentResult(True, legs, "", rej)


# ------------------------------------------------------------------ 미션 계획


def plan_mission(ctx: PlanContext) -> MissionPlan:
    sc, g, d = ctx.sc, ctx.grid, ctx.drone
    pos = sc.start
    e0 = 0.0
    segs: list[list[Leg]] = []
    for k, obj in enumerate(sc.objectives):
        tol = ctx.arrival_tol(k)
        res = ctx.plan_segment(pos, e0, obj, tol)
        if not res.ok:
            return MissionPlan(
                ok=False,
                legs_by_segment=segs,
                reason=res.reason,
                failed_segment=k,
                failed_detail={
                    "from": pos,
                    "to": (obj.x, obj.y),
                    "objective": obj.name,
                    "e0": e0,
                    "tolerance_m": tol,
                    "rejections": res.rejections,
                },
            )
        segs.append(res.legs)
        pos = (obj.x, obj.y)
        e0 = res.legs[-1].e_end
    plan = MissionPlan(ok=True, legs_by_segment=segs)
    _assemble(plan, ctx)
    return plan


def _seg_props(g: Grid, d, a, b):
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(math.ceil(L / g.res)))
    slow = False
    for k in range(1, n + 1):
        t = k / n
        if g.flow[g.cell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)] > 1.0:
            slow = True
            break
    return d.cruise_speed_mps * (d.low_flow_speed_factor if slow else 1.0)


def _assemble(plan: MissionPlan, ctx: PlanContext) -> None:
    sc, g, d = ctx.sc, ctx.grid, ctx.drone
    z = d.altitude_m
    sites = list(sc.landing_sites) + [sc.start, (sc.goal.x, sc.goal.y)]

    def abort_site(x, y):
        best = None
        for s in sorted(sites, key=lambda s: math.hypot(s[0] - x, s[1] - y)):
            if g.line_clear((x, y), s):
                best = s
                break
        return best

    def jam_at(x, y):
        return g.jam[g.cell(x, y)] > 0

    wps = [
        Waypoint(0, "takeoff", sc.start[0], sc.start[1], z, d.cruise_speed_mps, label="이륙",
                 abort_land_at=sc.start, link_expected=not jam_at(*sc.start))
    ]
    tags_used: list[int] = []
    total_t = TAKEOFF_S
    total_len = 0.0
    jam_len = 0.0
    for k, (obj, legs) in enumerate(zip(sc.objectives, plan.legs_by_segment)):
        for leg in legs:
            total_t += leg.time_s
            total_len += leg.length_m
            jam_len += leg.jam_len_m
            for v in range(1, len(leg.pts)):
                p, prev = leg.pts[v], leg.pts[v - 1]
                last = v == len(leg.pts) - 1
                speed = _seg_props(g, d, prev, p)
                kind, tag_id, dwell, label, tol, xfer = "goto", None, 0.0, "", 0.0, 0
                if last and leg.end_kind == "fix":
                    kind, tag_id, dwell, tol = "fix", leg.tag_id, d.tag_dwell_s, d.tag_detect_radius_m
                    label = f"태그 {leg.tag_id} 위치 보정"
                    if leg.tag_id not in tags_used:
                        tags_used.append(leg.tag_id)
                elif last and leg.end_kind == "dest":
                    tol = obj.tolerance_m
                    if obj.action == "land":
                        kind, label = "land", f"착륙: {obj.name}"
                    elif obj.action == "hover":
                        kind, dwell, label = "hover", obj.dwell_s, f"호버: {obj.name}"
                        total_t += obj.dwell_s
                    elif obj.action == "comm":
                        node = sc.tag(obj.node_id)  # type: ignore[arg-type]
                        kind = "comm" if node.role == "ble" else "read"
                        tag_id = node.id
                        xfer = stop_xfer_bytes(sc, k)
                        dwell = comm_time_s(d, node, xfer)
                        total_t += dwell
                        medium = "근접 BLE" if kind == "comm" else "디스플레이 코드 판독"
                        label = f"노드 {node.id} 교신({medium}, {xfer} B) + 위치 보정"
                        if node.id not in tags_used:
                            tags_used.append(node.id)
                    else:
                        label = f"통과: {obj.name}"
                wps.append(
                    Waypoint(
                        len(wps), kind, p[0], p[1], z, speed, tag_id, dwell,
                        link_expected=not jam_at(*p),
                        expected_sigma_m=leg.vertex_sigma[v],
                        abort_land_at=abort_site(*p),
                        label=label, segment=k, tolerance_m=tol, xfer_bytes=xfer,
                    )
                )
    total_t += LAND_S
    plan.waypoints = wps
    plan.total_time_s = total_t
    plan.total_length_m = total_len
    plan.jam_len_m = jam_len
    plan.tags_used = tags_used

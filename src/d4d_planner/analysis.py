"""전장 분석: 비행 가능 공간, 위협(재밍/광류저하) 비율, 태그 상태, 오차 예산."""
from __future__ import annotations

from dataclasses import dataclass, field

from .drift import max_lw
from .grid import Grid
from .model import PlanParams, Scenario


@dataclass
class Analysis:
    cells: int
    free_frac: float  # 비행 가능(팽창 후) 비율
    jam_frac: float
    low_flow_frac: float
    nominal_leg_m: float  # 태그 보정 후 정상 구역에서 다음 태그까지의 최대 거리
    low_flow_leg_m: float  # 광류 저하 구역 기준
    reachable: list[tuple[str, bool]] = field(default_factory=list)
    reachable_if_jam_allowed: list[tuple[str, bool]] = field(default_factory=list)
    tag_notes: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    network_notes: list[str] = field(default_factory=list)
    needs_jam_crossing: bool = False  # abort 정책이라 불가하지만 재밍 통과를 허용하면 도달 가능

    def to_dict(self) -> dict:
        return {
            "needs_jam_crossing": self.needs_jam_crossing,
            "network_notes": self.network_notes,
            "cells": self.cells,
            "free_frac": round(self.free_frac, 3),
            "jam_frac": round(self.jam_frac, 3),
            "low_flow_frac": round(self.low_flow_frac, 3),
            "nominal_leg_m": round(self.nominal_leg_m, 2),
            "low_flow_leg_m": round(self.low_flow_leg_m, 2),
            "reachable": self.reachable,
            "reachable_if_jam_allowed": self.reachable_if_jam_allowed,
            "tag_notes": self.tag_notes,
            "blocking": self.blocking,
            "warnings": self.warnings,
        }


def analyze(sc: Scenario, grid: Grid, params: PlanParams) -> Analysis:
    d = sc.drone
    n = grid.n
    free = sum(1 for b in grid.blocked if not b)
    jam = sum(1 for v in grid.jam if v > 0)
    lowf = sum(1 for v in grid.flow if v > 1.0)
    e_lim = d.tag_detect_radius_m / params.z
    leg = max_lw(d.tag_sigma_m, e_lim, d)
    low_mult = max([z.value for z in sc.zones if z.kind == "low_flow"] or [1.0])
    a = Analysis(
        cells=n,
        free_frac=free / n,
        jam_frac=jam / n,
        low_flow_frac=lowf / n,
        nominal_leg_m=leg,
        low_flow_leg_m=leg / low_mult,
    )

    # 연결성
    start_cell = grid.cell(*sc.start)
    if grid.blocked[start_cell]:
        a.blocking.append("출발점이 안전 여유(기체 반경+마진) 안쪽에 있어 비행 불가 셀입니다. 출발점 또는 장애물을 조정하세요.")
    seen = grid.flood(start_cell)
    for o in sc.objectives:
        c = grid.cell(o.x, o.y)
        if grid.blocked[c]:
            a.blocking.append(f"목표 '{o.name}'({o.x:.1f},{o.y:.1f})가 비행 불가 셀입니다(장애물/금지/재밍 구역 또는 여유 부족).")
        a.reachable.append((o.name, bool(seen[c])))

    if sc.link_policy == "abort" and any(not ok for _, ok in a.reachable):
        relaxed = sc.clone()
        relaxed.link_policy = "continue_mission"
        g2 = Grid(relaxed, params)
        seen2 = g2.flood(g2.cell(*sc.start))
        a.reachable_if_jam_allowed = [(o.name, bool(seen2[g2.cell(o.x, o.y)])) for o in sc.objectives]
        if any(ok2 and not ok for (_, ok), (_, ok2) in zip(a.reachable, a.reachable_if_jam_allowed)):
            a.needs_jam_crossing = True
            a.blocking.append(
                "link_policy=abort 에서는 재밍 구역을 통과할 수 없어 목표에 도달할 수 없습니다. "
                "재밍 구역 통과가 필수라면 link_policy=continue_mission(조종 불가 구간 감수)이 필요합니다."
            )
    for name, ok in a.reachable:
        if not ok and not a.blocking:
            a.blocking.append(f"목표 '{name}'로 가는 비행 가능 경로가 없습니다.")

    # 태그 상태
    for t in sc.tags:
        c = grid.cell(t.x, t.y)
        if not grid.inside(t.x, t.y) or grid.blocked[c]:
            a.tag_notes.append(f"태그 {t.id}({t.x:.1f},{t.y:.1f})는 비행 불가 셀에 있어 경유 지점으로 쓸 수 없습니다.")
        elif grid.jam[c] > 0:
            a.tag_notes.append(f"태그 {t.id}는 재밍 구역 안에 있습니다(통신 두절 구간 보정 지점).")
    if not sc.tags:
        a.warnings.append("등록된 태그가 없습니다. 위치 보정 없이는 오차가 누적됩니다.")

    # 네트워크 노드
    nodes = sc.nodes
    if nodes:
        n_ble = sum(1 for t in nodes if t.role == "ble")
        n_mb = len(nodes) - n_ble
        a.network_notes.append(f"네트워크 노드 {len(nodes)}개 (BLE {n_ble}, 광학 메일박스 {n_mb}), 메시지 {len(sc.messages)}건")
        for t in nodes:
            jam = grid.jam[grid.cell(t.x, t.y)]
            if t.role == "ble" and jam > 0:
                r = d.node_range_m(t) * max(0.05, 1.0 - d.ble_jam_shrink * jam)
                a.network_notes.append(
                    f"BLE 노드 {t.id}는 재밍 {jam:.1f} 구역 안: 유효 교신 거리 약 {r:.2f} m (교신 고도 {d.comm_altitude_m} m)"
                    + (" -> 고도보다 짧아 교신 불가, 광학 메일박스 권장" if r <= d.comm_altitude_m else "")
                )
    elif sc.messages:
        a.blocking.append("메시지는 있는데 노드(role=mailbox|ble)가 없습니다.")

    # 카메라 화각 대비 태그 인식 반경
    half = d.cam_half_width_m
    if d.tag_detect_radius_m > half + 1e-6:
        a.warnings.append(
            f"태그 인식 반경 {d.tag_detect_radius_m:.2f} m 가 카메라 화각 기준 반폭 {half:.2f} m "
            f"(고도 {d.altitude_m} m, 수평 화각 {d.cam_hfov_deg:.0f}°)보다 큽니다. 태그가 화면 밖일 수 있으니 반경을 줄이거나 고도를 높이세요."
        )

    if a.nominal_leg_m < 3.0:
        a.warnings.append(
            f"태그 간 허용 거리가 {a.nominal_leg_m:.1f} m 로 짧습니다. 드리프트 파라미터 또는 태그 인식 반경을 점검하세요."
        )
    return a

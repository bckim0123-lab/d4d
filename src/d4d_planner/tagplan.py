"""태그 설치 위치 제안: 오차 예산이 부족한 구간에 태그를 추가한다."""
from __future__ import annotations

import math

from .drift import max_lw, sigma
from .model import Tag
from .planner import PlanContext

SUGGEST_ID_BASE = 900
SAFETY = 0.8  # 태그 인식 반경의 80% 안에서 도착하도록 간격을 잡는다
NEAR_EXISTING_M = 0.5


def suggest_tags(ctx: PlanContext, a_xy, e0: float, b_xy, next_id: int, tol: float, max_new: int = 40) -> list[Tag]:
    """a -> b 최선 경로(드리프트 제약 무시)를 따라가며, 오차가 한계에 닿기 전에 태그를 배치."""
    g, d, z = ctx.grid, ctx.drone, ctx.params.z
    ex = ctx.expand(a_xy, float("inf"))
    tc = g.cell(*b_xy)
    if tc not in ex.dist:
        return []
    cells = []
    c = tc
    while c != -1:
        cells.append(c)
        c = ex.par[c]
    cells.reverse()
    pts = [g.xy(c) for c in cells]

    e_base, lw = e0, 0.0
    new: list[Tag] = []
    e_lim = SAFETY * d.tag_detect_radius_m / z
    for k in range(1, len(pts)):
        a, b = pts[k - 1], pts[k]
        ds = math.hypot(b[0] - a[0], b[1] - a[1])
        idx = g.cell(*b)
        # 이미 설치된 태그 바로 옆을 지나면 그 태그에서 보정된 것으로 본다(플래너가 경유 노드로 사용)
        if any(math.hypot(b[0] - t.x, b[1] - t.y) <= NEAR_EXISTING_M for t in ctx.usable):
            e_base, lw = d.tag_sigma_m, 0.0
            continue
        lw_next = lw + ds * g.flow[idx]
        e_next = sigma(e_base, lw_next, d)
        clear_ok = z * e_next <= g.dist_obs[idx] - d.radius_m
        if e_next > e_lim or not clear_ok:
            if lw == 0.0 and e_base <= d.tag_sigma_m + 1e-9:
                # 보정 직후에도 위반: 통로가 너무 좁아 태그로 해결 불가
                break
            new.append(Tag(next_id + len(new), a[0], a[1], suggested=True))
            if len(new) >= max_new:
                break
            e_base, lw = d.tag_sigma_m, 0.0
            lw_next = ds * g.flow[idx]
        lw = lw_next
    # 도착 정밀도 요건(중간 목표/착륙 허용오차)을 못 맞추면 목표 지점에 태그를 둔다
    if len(new) < max_new and z * sigma(e_base, lw, d) > SAFETY * tol:
        near = any(math.hypot(b_xy[0] - t.x, b_xy[1] - t.y) <= NEAR_EXISTING_M for t in ctx.usable)
        if not near:
            new.append(Tag(next_id + len(new), b_xy[0], b_xy[1], suggested=True))
    return new


def filter_new(existing: list[Tag], new: list[Tag], min_dist: float = 1.0) -> list[Tag]:
    out = []
    for t in new:
        if all(math.hypot(t.x - e.x, t.y - e.y) >= min_dist for e in existing + out):
            out.append(t)
    return out

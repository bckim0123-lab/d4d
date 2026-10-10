"""전장 래스터화: 장애물/재밍/광류저하 맵, 이격거리(EDT), 비용 맵."""
from __future__ import annotations

import math
from collections import deque

from .model import PlanParams, Scenario

INF = float("inf")
_BIG = 1e12


def _edt_1d(f: list[float]) -> list[float]:
    """Felzenszwalb 1D 제곱거리 변환."""
    n = len(f)
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    z[0] = -INF
    z[1] = INF
    for q in range(1, n):
        while True:
            vk = v[k]
            s = ((f[q] + q * q) - (f[vk] + vk * vk)) / (2 * q - 2 * vk)
            if s <= z[k]:
                k -= 1
            else:
                break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = INF
    out = [0.0] * n
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        out[q] = (q - v[k]) ** 2 + f[v[k]]
    return out


def edt_cells(mask, nx: int, ny: int) -> list[float]:
    """mask가 참인 셀까지의 유클리드 거리(셀 단위). 마스크가 비면 매우 큰 값."""
    cols = []
    for i in range(nx):
        f = [0.0 if mask[j * nx + i] else _BIG for j in range(ny)]
        cols.append(_edt_1d(f))
    out = [0.0] * (nx * ny)
    for j in range(ny):
        row = _edt_1d([cols[i][j] for i in range(nx)])
        base = j * nx
        for i in range(nx):
            out[base + i] = math.sqrt(row[i])
    return out


NEIGH = [
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
]


class Grid:
    def __init__(self, sc: Scenario, params: PlanParams):
        self.sc = sc
        self.res = sc.field.resolution_m
        self.W = sc.field.width_m
        self.H = sc.field.height_m
        self.nx = int(math.ceil(self.W / self.res))
        self.ny = int(math.ceil(self.H / self.res))
        n = self.nx * self.ny
        self.n = n
        d = sc.drone
        self.raw = bytearray(n)  # 실제 장애물 + 금지구역
        self.jam = [0.0] * n
        self.flow = [1.0] * n

        for z in sc.zones:
            x0, y0, x1, y1 = z.shape.bbox()
            i0 = max(0, int(x0 / self.res) - 1)
            i1 = min(self.nx - 1, int(x1 / self.res) + 1)
            j0 = max(0, int(y0 / self.res) - 1)
            j1 = min(self.ny - 1, int(y1 / self.res) + 1)
            for j in range(j0, j1 + 1):
                cy = (j + 0.5) * self.res
                for i in range(i0, i1 + 1):
                    if not z.shape.contains((i + 0.5) * self.res, cy):
                        continue
                    idx = j * self.nx + i
                    if z.kind in ("obstacle", "keepout"):
                        self.raw[idx] = 1
                    elif z.kind == "jam":
                        self.jam[idx] = max(self.jam[idx], z.value)
                    elif z.kind == "low_flow":
                        self.flow[idx] = max(self.flow[idx], z.value)

        # 장애물까지의 이격거리(m). 필드 경계도 장애물로 취급.
        has_obs = any(self.raw)
        d_obs = edt_cells(self.raw, self.nx, self.ny) if has_obs else [_BIG] * n
        self.dist_obs = [0.0] * n
        for j in range(self.ny):
            cy = (j + 0.5) * self.res
            for i in range(self.nx):
                idx = j * self.nx + i
                cx = (i + 0.5) * self.res
                if self.raw[idx]:
                    self.dist_obs[idx] = 0.0
                    continue
                edge = min(cx, self.W - cx, cy, self.H - cy)
                self.dist_obs[idx] = min(max(0.0, d_obs[idx] * self.res - self.res / 2), edge)

        jam_mask = [1 if v > 0 else 0 for v in self.jam]
        has_jam = any(jam_mask)
        d_jam = edt_cells(jam_mask, self.nx, self.ny) if has_jam else [_BIG] * n
        self.dist_jam = [max(0.0, v * self.res - self.res / 2) if not jam_mask[i] else 0.0 for i, v in enumerate(d_jam)]

        self.set_params(params)

    # ------------------------------------------------------------ 파라미터 의존 맵
    def set_params(self, params: PlanParams) -> None:
        self.params = params
        d = self.sc.drone
        r_infl = d.radius_m + params.margin_m
        abort = self.sc.link_policy == "abort"
        self.blocked = bytearray(self.n)
        self.cell_cost = [0.0] * self.n
        for idx in range(self.n):
            b = self.dist_obs[idx] < r_infl
            if abort and (self.jam[idx] > 0 or self.dist_jam[idx] < params.jam_margin_m):
                b = True
            self.blocked[idx] = 1 if b else 0
            pen = 1.0 - (self.dist_obs[idx] - r_infl) / params.pref_clear_m
            pen = min(1.0, max(0.0, pen))
            self.cell_cost[idx] = self.flow[idx] + params.w_clear * pen + params.w_jam * self.jam[idx]
        # 충돌 판정(몬테카를로용): 기체 반경 안에 장애물이 들어오면 충돌
        self.collide = [1 if self.dist_obs[i] < d.radius_m else 0 for i in range(self.n)]

    # ------------------------------------------------------------ 좌표
    def inside(self, x: float, y: float) -> bool:
        return 0.0 <= x <= self.W and 0.0 <= y <= self.H

    def cell(self, x: float, y: float) -> int:
        i = min(self.nx - 1, max(0, int(x / self.res)))
        j = min(self.ny - 1, max(0, int(y / self.res)))
        return j * self.nx + i

    def xy(self, idx: int) -> tuple[float, float]:
        return ((idx % self.nx + 0.5) * self.res, (idx // self.nx + 0.5) * self.res)

    def is_blocked_xy(self, x: float, y: float) -> bool:
        return (not self.inside(x, y)) or bool(self.blocked[self.cell(x, y)])

    # ------------------------------------------------------------ 직선 질의
    def _samples(self, p, q):
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        n = max(1, int(math.ceil(L / (self.res * 0.5))))
        return L, n

    def line_clear(self, p, q) -> bool:
        L, n = self._samples(p, q)
        for k in range(n + 1):
            t = k / n
            x = p[0] + (q[0] - p[0]) * t
            y = p[1] + (q[1] - p[1]) * t
            if self.blocked[self.cell(x, y)]:
                return False
        return True

    def line_cost(self, p, q) -> float:
        L, n = self._samples(p, q)
        ds = L / n
        c = 0.0
        for k in range(1, n + 1):
            t = k / n
            c += ds * self.cell_cost[self.cell(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)]
        return c

    # ------------------------------------------------------------ 연결성
    def flood(self, src: int) -> bytearray:
        seen = bytearray(self.n)
        if self.blocked[src]:
            return seen
        seen[src] = 1
        dq = deque([src])
        nx, ny = self.nx, self.ny
        while dq:
            c = dq.popleft()
            ci, cj = c % nx, c // nx
            for di, dj, _ in NEIGH:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < nx and 0 <= nj < ny):
                    continue
                n = nj * nx + ni
                if seen[n] or self.blocked[n]:
                    continue
                if di and dj and (self.blocked[cj * nx + ni] or self.blocked[nj * nx + ci]):
                    continue
                seen[n] = 1
                dq.append(n)
        return seen

"""추측항법(광류 + 자이로) 오차 모델."""
from __future__ import annotations

import math

from .model import DroneSpec


def sigma(e0: float, lw_m: float, drone: DroneSpec) -> float:
    """마지막 위치 보정 이후 가중 이동거리 lw_m에서의 1-sigma 위치 오차.

    e0: 구간 시작 시 오차, lw_m: sum(ds * 드리프트 배수).
    scale/yaw 오차는 거리에 비례, 랜덤워크는 sqrt(거리)에 비례한다.
    """
    c = drone.drift_c
    return math.sqrt(e0 * e0 + (c * lw_m) ** 2 + drone.noise_per_sqrt_m**2 * lw_m)


def max_lw(e0: float, e_lim: float, drone: DroneSpec) -> float:
    """sigma(e0, lw) <= e_lim 을 만족하는 최대 lw (이분탐색)."""
    if e0 >= e_lim:
        return 0.0
    lo, hi = 0.0, 1.0
    while sigma(e0, hi, drone) < e_lim and hi < 1e6:
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if sigma(e0, mid, drone) <= e_lim:
            lo = mid
        else:
            hi = mid
    return lo

"""도메인 모델: 시나리오(전장), 드론 사양, 계획 파라미터, 웨이포인트."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional, Union

# ---------------------------------------------------------------- 도형


@dataclass(frozen=True)
class Shape:
    """rect:(x0,y0,x1,y1)  circle:(cx,cy,r)  polygon:((x,y),...)"""

    kind: str
    pts: tuple

    def bbox(self) -> tuple[float, float, float, float]:
        if self.kind == "rect":
            return self.pts
        if self.kind == "circle":
            cx, cy, r = self.pts
            return (cx - r, cy - r, cx + r, cy + r)
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float) -> bool:
        if self.kind == "rect":
            x0, y0, x1, y1 = self.pts
            return x0 <= x <= x1 and y0 <= y <= y1
        if self.kind == "circle":
            cx, cy, r = self.pts
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
        # polygon: ray casting
        inside = False
        pts = self.pts
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


# ---------------------------------------------------------------- 시나리오 요소

ZONE_KINDS = ("obstacle", "keepout", "jam", "low_flow")


@dataclass
class Zone:
    kind: str  # obstacle | keepout | jam | low_flow
    name: str
    shape: Shape
    value: float = 1.0  # jam: 강도(0~1], low_flow: 드리프트 배수(>=1)


# 노드 역할 (스파이더 코드 네트워크)
#   marker : 종이 AprilTag. 위치 보정만 한다. 전자장치 없음.
#   mailbox: AprilTag + ESP32 디스플레이. 드론이 카메라로 코드를 읽어(광학) 메시지를 주고받는다. RF를 쓰지 않아 재밍 무관.
#   ble    : AprilTag + ESP32 BLE. 태그 위에서 근접 BLE로 메시지를 주고받는다. 재밍 강도에 따라 성공률이 떨어진다.
TAG_ROLES = ("marker", "mailbox", "ble")
MSG_ENDPOINTS = ("start", "goal")  # 메시지 출발/도착이 지상국(출발점/착륙지점)인 경우


@dataclass
class Tag:
    id: int
    x: float
    y: float
    suggested: bool = False  # True면 에이전트가 제안한(아직 설치되지 않은) 태그
    role: str = "marker"  # TAG_ROLES
    ble_range_m: float = 0.0  # 0이면 DroneSpec.ble_range_m 사용
    payload_bytes: int = 0  # 교신 때마다 노드가 추가로 올리는 상태 보고 바이트
    role_suggested: bool = False  # True면 에이전트가 역할 변경(ble -> mailbox)을 제안한 노드

    @property
    def is_node(self) -> bool:
        return self.role != "marker"


@dataclass
class Message:
    """드론이 운반하는 메시지. src에서 싣고 dst에 내려놓는다. 'start'/'goal'은 지상국."""

    name: str
    src: Union[int, str]
    dst: Union[int, str]
    bytes: int = 64
    priority: int = 1  # 작을수록 먼저 처리


@dataclass
class Objective:
    name: str
    x: float
    y: float
    action: str = "pass"  # pass | hover | land | comm(노드 교신, 내부 생성)
    dwell_s: float = 0.0
    tolerance_m: float = 2.0
    node_id: Optional[int] = None  # action == comm 일 때 교신할 노드(태그) id


@dataclass
class Field:
    width_m: float
    height_m: float
    resolution_m: float = 0.25


@dataclass
class DroneSpec:
    """기체/센서 사양. 값은 가정치이며 실측으로 보정해야 한다."""

    radius_m: float = 0.20  # 기체 반경(프롭 포함)
    cruise_speed_mps: float = 0.6
    altitude_m: float = 1.2
    endurance_s: float = 420.0
    reserve_frac: float = 0.30  # 배터리 예비율
    # 추측항법(MTF-01P 광류 + 자이로) 오차 모델, 1-sigma
    flow_scale_sigma: float = 0.03  # 이동거리 대비 비율
    yaw_drift_sigma: float = 0.02  # rad, 측방 오차로 환산
    noise_per_sqrt_m: float = 0.02  # m/sqrt(m), 랜덤워크
    # AprilTag 위치 보정
    tag_sigma_m: float = 0.05
    tag_detect_radius_m: float = 1.0  # 이 수평거리 안에서 태그 인식 가능
    tag_detect_prob: float = 0.95  # 1회 시도 인식 확률(최대 3회)
    tag_dwell_s: float = 2.0
    low_flow_speed_factor: float = 0.5  # 광류 저하 구역에서 감속 비율
    cam_hfov_deg: float = 55.0  # OV2640 수평 화각 (기기정보/05, 실측 보정값)
    # XIAO -> FC UART 명령 파라미터(펌웨어 연동용)
    cmd_rate_hz: float = 10.0
    cmd_timeout_ms: float = 500.0
    # --- 스파이더 코드 네트워크: 노드 교신 모델 (기기정보/08 BLE 실측 + 가정치) ---
    ble_range_m: float = 3.0  # 재밍 없는 곳에서 근접 BLE가 되는 수평 거리(보수적 가정, 실측 -35~-55 dBm @근접)
    ble_link_prob: float = 0.98  # 범위 안 1회 시도 성공 확률
    ble_jam_shrink: float = 0.7  # 재밍 강도 1.0에서 유효 범위가 (1-값)배로 줄어든다 (가정치)
    ble_rate_Bps: float = 7000.0  # NimBLE NUS 실측 약 7 KB/s
    comm_altitude_m: float = 0.6  # 교신 고도. 200 mm 태그는 0.45 m보다 가까우면 인식 불가
    comm_dwell_s: float = 2.0  # 하강/상승 + 핸드셰이크 고정 시간
    read_code_prob: float = 0.9  # 디스플레이 코드(mailbox) 1회 판독 성공 확률 (가정치)
    optical_rate_Bps: float = 300.0  # 디스플레이 코드 전송률 (가정치, QR 1프레임 약 150 B × 2 fps)

    @property
    def drift_c(self) -> float:
        return math.hypot(self.flow_scale_sigma, self.yaw_drift_sigma)

    @property
    def cam_half_width_m(self) -> float:
        """비행 고도에서 카메라가 바닥에서 보는 반폭(수평 화각 기준)."""
        return self.altitude_m * math.tan(math.radians(self.cam_hfov_deg) / 2)

    def node_range_m(self, tag: "Tag") -> float:
        return tag.ble_range_m if tag.ble_range_m > 0 else self.ble_range_m


@dataclass
class PlanParams:
    """플래너/에이전트가 조정하는 파라미터."""

    margin_m: float = 0.30  # 기체 반경 외 추가 안전 여유
    jam_margin_m: float = 0.60  # abort 정책에서 재밍 구역 경계 이격
    z: float = 2.5  # 오차 허용 배수(z * sigma)
    w_clear: float = 2.0  # 장애물 근접 페널티
    pref_clear_m: float = 1.0
    w_jam: float = 3.0  # continue 정책에서 재밍 구역 통과 페널티
    mc_runs: int = 200
    min_success: float = 0.95
    max_collision: float = 0.01
    min_delivery: float = 0.90  # 메시지별 전달률 하한 (네트워크 시나리오에서만 의미)
    seed: int = 7


@dataclass
class Scenario:
    name: str
    field: Field
    start: tuple[float, float]
    objectives: list[Objective]  # 마지막 원소는 항상 land
    tags: list[Tag]
    zones: list[Zone]
    landing_sites: list[tuple[float, float]]
    link_policy: str  # abort | continue_mission
    drone: DroneSpec = field(default_factory=DroneSpec)
    messages: list[Message] = field(default_factory=list)

    @property
    def goal(self) -> Objective:
        return self.objectives[-1]

    @property
    def nodes(self) -> list[Tag]:
        return [t for t in self.tags if t.is_node]

    def tag(self, tid: int) -> Optional[Tag]:
        for t in self.tags:
            if t.id == tid:
                return t
        return None

    def clone(self) -> "Scenario":
        return Scenario(
            self.name,
            self.field,
            self.start,
            [replace(o) for o in self.objectives],
            [replace(t) for t in self.tags],
            list(self.zones),
            list(self.landing_sites),
            self.link_policy,
            self.drone,
            [replace(m) for m in self.messages],
        )


# ---------------------------------------------------------------- 계획 결과

# comm: 노드 위에서 근접 BLE 교신(+위치 보정), read: 디스플레이 코드 판독(광학 교신, +위치 보정)
WP_KINDS = ("takeoff", "goto", "fix", "hover", "land", "comm", "read")


@dataclass
class Waypoint:
    idx: int
    kind: str  # takeoff | goto | fix | hover | land | comm | read
    x: float
    y: float
    z: float
    speed: float
    tag_id: Optional[int] = None
    dwell_s: float = 0.0
    link_expected: bool = True  # False면 재밍 구역: RC/텔레메트리 두절 예상
    expected_sigma_m: float = 0.0
    abort_land_at: Optional[tuple[float, float]] = None
    label: str = ""
    segment: int = 0
    tolerance_m: float = 0.0
    xfer_bytes: int = 0  # comm/read 에서 주고받는 바이트(메시지 + 노드 상태 보고)


@dataclass
class Leg:
    pts: list[tuple[float, float]]
    e0: float
    e_end: float
    vertex_sigma: list[float]
    end_kind: str  # fix | dest
    tag_id: Optional[int]
    lw_m: float
    length_m: float
    jam_len_m: float
    flow_len_m: float
    time_s: float
    min_clear_margin_m: float


@dataclass
class MissionPlan:
    ok: bool
    legs_by_segment: list[list[Leg]] = field(default_factory=list)
    waypoints: list[Waypoint] = field(default_factory=list)
    reason: str = ""
    failed_segment: int = -1
    failed_detail: dict = field(default_factory=dict)
    total_time_s: float = 0.0
    total_length_m: float = 0.0
    jam_len_m: float = 0.0
    tags_used: list[int] = field(default_factory=list)

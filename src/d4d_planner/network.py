"""스파이더 코드 네트워크: 메시지 운반 순서와 노드 교신 비용.

드론은 RF가 막힌 구역에서 '데이터 뮬'로 동작한다. 메시지는 src 노드(또는 지상국 start)에서 싣고
dst 노드(또는 착륙지점 goal)에 내려놓는다. 여기서는 어떤 노드를 어떤 순서로 들러야 하는지와,
노드에서 주고받는 바이트 수/소요 시간을 결정론적으로 계산한다.
"""
from __future__ import annotations

from typing import Union

from .model import DroneSpec, Message, Objective, Scenario, Tag

Endpoint = Union[int, str]


def comm_stop_order(messages: list[Message]) -> list[int]:
    """메시지 src -> dst 선후 관계를 만족하는 노드 방문 순서.

    규칙(결정론적):
      - 메시지는 priority, 입력 순서대로 처리한다.
      - src가 노드이고 아직 순서에 없으면 뒤에 붙인다. 있으면 첫 등장 위치를 src 위치로 본다.
      - dst가 노드인데 src 위치 뒤에 등장하지 않으면 뒤에 붙인다(필요하면 같은 노드를 다시 방문).
    """
    order: list[int] = []
    for m in sorted(messages, key=lambda m: m.priority):
        src_idx = -1
        if isinstance(m.src, int):
            if m.src in order:
                src_idx = order.index(m.src)
            else:
                order.append(m.src)
                src_idx = len(order) - 1
        if isinstance(m.dst, int):
            if not any(n == m.dst for n in order[src_idx + 1 :]):
                order.append(m.dst)
    return order


def comm_objectives(sc_tags: list[Tag], messages: list[Message], drone: DroneSpec) -> list[Objective]:
    """방문 순서를 플래너가 쓰는 Objective(action=comm)로 변환."""
    tags = {t.id: t for t in sc_tags}
    out = []
    for i, nid in enumerate(comm_stop_order(messages)):
        t = tags[nid]
        out.append(
            Objective(
                name=f"노드 {nid} 교신",
                x=t.x,
                y=t.y,
                action="comm",
                dwell_s=0.0,
                tolerance_m=drone.tag_detect_radius_m,
                node_id=nid,
            )
        )
    return out


def node_visit_index(objectives: list[Objective]) -> dict[int, list[int]]:
    """노드 id -> 그 노드를 교신 목적으로 방문하는 구간(objective) 인덱스 목록."""
    out: dict[int, list[int]] = {}
    for k, o in enumerate(objectives):
        if o.action == "comm" and o.node_id is not None:
            out.setdefault(o.node_id, []).append(k)
    return out


def messages_at_stop(sc: Scenario, segment: int) -> tuple[list[Message], list[Message]]:
    """구간 segment의 교신에서 (싣는 메시지, 내려놓는 메시지).

    같은 노드를 여러 번 방문하면, 싣기는 첫 방문에서, 내려놓기는 src 방문 이후 첫 방문에서 한다.
    """
    obj = sc.objectives[segment]
    if obj.action != "comm":
        return [], []
    visits = node_visit_index(sc.objectives)
    pick: list[Message] = []
    drop: list[Message] = []
    for m in sc.messages:
        if m.src == obj.node_id and visits[m.src][0] == segment:
            pick.append(m)
        if isinstance(m.dst, int) and m.dst == obj.node_id:
            src_seg = -1 if not isinstance(m.src, int) else visits[m.src][0]
            later = [k for k in visits[m.dst] if k > src_seg]
            if later and later[0] == segment:
                drop.append(m)
    return pick, drop


def stop_xfer_bytes(sc: Scenario, segment: int) -> int:
    pick, drop = messages_at_stop(sc, segment)
    node = sc.tag(sc.objectives[segment].node_id)  # type: ignore[arg-type]
    extra = node.payload_bytes if node else 0
    return sum(m.bytes for m in pick + drop) + extra


def comm_time_s(drone: DroneSpec, node: Tag, xfer_bytes: int) -> float:
    rate = drone.ble_rate_Bps if node.role == "ble" else drone.optical_rate_Bps
    rate = max(rate, 1.0)
    return drone.comm_dwell_s + xfer_bytes / rate


def endpoint_label(e: Endpoint) -> str:
    if e == "start":
        return "지상국(출발)"
    if e == "goal":
        return "지상국(착륙)"
    return f"노드 {e}"

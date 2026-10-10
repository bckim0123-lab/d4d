"""시나리오 JSON 로딩 및 검증."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .model import (
    MSG_ENDPOINTS,
    TAG_ROLES,
    ZONE_KINDS,
    DroneSpec,
    Field,
    Message,
    Objective,
    Scenario,
    Shape,
    Tag,
    Zone,
)
from .network import comm_objectives


class ScenarioError(ValueError):
    pass


def _num(d: dict, key: str, ctx: str) -> float:
    if key not in d:
        raise ScenarioError(f"{ctx}: '{key}' 필드가 없습니다.")
    try:
        return float(d[key])
    except (TypeError, ValueError):
        raise ScenarioError(f"{ctx}: '{key}'는 숫자여야 합니다 ({d[key]!r}).")


def _shape(d: dict, ctx: str) -> Shape:
    kind = d.get("shape")
    if kind == "rect":
        x, y, w, h = (_num(d, k, ctx) for k in ("x", "y", "w", "h"))
        if w <= 0 or h <= 0:
            raise ScenarioError(f"{ctx}: rect의 w,h는 양수여야 합니다.")
        return Shape("rect", (x, y, x + w, y + h))
    if kind == "circle":
        x, y, r = (_num(d, k, ctx) for k in ("x", "y", "r"))
        if r <= 0:
            raise ScenarioError(f"{ctx}: circle의 r은 양수여야 합니다.")
        return Shape("circle", (x, y, r))
    if kind == "polygon":
        pts = d.get("points")
        if not pts or len(pts) < 3:
            raise ScenarioError(f"{ctx}: polygon은 3개 이상의 points가 필요합니다.")
        return Shape("polygon", tuple((float(p[0]), float(p[1])) for p in pts))
    raise ScenarioError(f"{ctx}: shape는 rect|circle|polygon 중 하나여야 합니다 ({kind!r}).")


def parse_scenario(data: dict) -> Scenario:
    f = data.get("field")
    if not f:
        raise ScenarioError("field 항목이 필요합니다.")
    fld = Field(
        _num(f, "width_m", "field"),
        _num(f, "height_m", "field"),
        float(f.get("resolution_m", 0.25)),
    )
    if fld.width_m <= 0 or fld.height_m <= 0 or fld.resolution_m <= 0:
        raise ScenarioError("field 크기/해상도는 양수여야 합니다.")

    def inside(x: float, y: float) -> bool:
        return 0 <= x <= fld.width_m and 0 <= y <= fld.height_m

    s = data.get("start")
    if not s:
        raise ScenarioError("start 항목이 필요합니다.")
    start = (_num(s, "x", "start"), _num(s, "y", "start"))
    if not inside(*start):
        raise ScenarioError("start가 필드 밖에 있습니다.")

    objectives: list[Objective] = []
    for i, o in enumerate(data.get("objectives", [])):
        ctx = f"objectives[{i}]"
        ob = Objective(
            str(o.get("name", f"목표{i + 1}")),
            _num(o, "x", ctx),
            _num(o, "y", ctx),
            str(o.get("action", "hover")),
            float(o.get("dwell_s", 0.0)),
            float(o.get("tolerance_m", 2.0)),
        )
        if ob.action not in ("pass", "hover"):
            raise ScenarioError(f"{ctx}: action은 pass|hover 중 하나입니다.")
        if not inside(ob.x, ob.y):
            raise ScenarioError(f"{ctx}: 필드 밖에 있습니다.")
        objectives.append(ob)

    g = data.get("goal")
    if not g:
        raise ScenarioError("goal 항목이 필요합니다.")
    goal = Objective(
        str(g.get("name", "착륙")),
        _num(g, "x", "goal"),
        _num(g, "y", "goal"),
        "land",
        0.0,
        float(g.get("tolerance_m", 1.0)),
    )
    if not inside(goal.x, goal.y):
        raise ScenarioError("goal이 필드 밖에 있습니다.")
    objectives.append(goal)

    tags: list[Tag] = []
    seen = set()
    for i, t in enumerate(data.get("tags", [])):
        ctx = f"tags[{i}]"
        tid = int(_num(t, "id", ctx))
        if tid in seen:
            raise ScenarioError(f"{ctx}: 태그 id {tid}가 중복되었습니다.")
        seen.add(tid)
        role = str(t.get("role", "marker"))
        if role not in TAG_ROLES:
            raise ScenarioError(f"{ctx}: role은 {'|'.join(TAG_ROLES)} 중 하나여야 합니다 ({role!r}).")
        ble_range = float(t.get("ble_range_m", 0.0))
        if ble_range < 0:
            raise ScenarioError(f"{ctx}: ble_range_m은 0 이상이어야 합니다.")
        payload = int(t.get("payload_bytes", 0))
        if payload < 0:
            raise ScenarioError(f"{ctx}: payload_bytes는 0 이상이어야 합니다.")
        tag = Tag(tid, _num(t, "x", ctx), _num(t, "y", ctx), role=role, ble_range_m=ble_range, payload_bytes=payload)
        if not inside(tag.x, tag.y):
            raise ScenarioError(f"{ctx}: 필드 밖에 있습니다.")
        tags.append(tag)

    zones: list[Zone] = []
    for i, z in enumerate(data.get("zones", [])):
        ctx = f"zones[{i}]"
        kind = z.get("kind")
        if kind not in ZONE_KINDS:
            raise ScenarioError(f"{ctx}: kind는 {'|'.join(ZONE_KINDS)} 중 하나여야 합니다.")
        default = {"jam": 1.0, "low_flow": 3.0}.get(kind, 1.0)
        val = float(z.get("value", default))
        if kind == "jam" and not (0 < val <= 1):
            raise ScenarioError(f"{ctx}: jam의 value(강도)는 (0,1] 범위입니다.")
        if kind == "low_flow" and val < 1:
            raise ScenarioError(f"{ctx}: low_flow의 value(드리프트 배수)는 1 이상입니다.")
        zones.append(Zone(kind, str(z.get("name", f"{kind}{i}")), _shape(z, ctx), val))

    sites = [
        (_num(s_, "x", "landing_sites"), _num(s_, "y", "landing_sites"))
        for s_ in data.get("landing_sites", [])
    ]

    policy = data.get("link_policy", "abort")
    if policy not in ("abort", "continue_mission"):
        raise ScenarioError("link_policy는 abort | continue_mission 중 하나입니다.")

    drone = DroneSpec()
    for k, v in (data.get("drone") or {}).items():
        if not hasattr(drone, k) or isinstance(getattr(DroneSpec, k, None), property):
            raise ScenarioError(f"drone.{k}: 알 수 없는 항목입니다.")
        setattr(drone, k, float(v))

    messages = _messages(data.get("messages", []), tags)
    # 네트워크 교신 지점은 사용자 목표보다 먼저 방문한다(결정론적 규칙). 마지막은 항상 착륙.
    objectives = comm_objectives(tags, messages, drone) + objectives

    return Scenario(
        str(data.get("name", "unnamed")), fld, start, objectives, tags, zones, sites, policy, drone, messages
    )


def _endpoint(v, ctx: str, tags: dict):
    if isinstance(v, str):
        if v in MSG_ENDPOINTS:
            return v
        if v.lstrip("-").isdigit():
            v = int(v)
        else:
            raise ScenarioError(f"{ctx}: 'start'|'goal'|노드 id 여야 합니다 ({v!r}).")
    try:
        nid = int(v)
    except (TypeError, ValueError):
        raise ScenarioError(f"{ctx}: 'start'|'goal'|노드 id 여야 합니다 ({v!r}).")
    if nid not in tags:
        raise ScenarioError(f"{ctx}: 태그 {nid}가 없습니다.")
    if not tags[nid].is_node:
        raise ScenarioError(
            f"{ctx}: 태그 {nid}는 role=marker(종이 태그)라 메시지를 보관할 수 없습니다. role을 mailbox|ble 로 바꾸세요."
        )
    return nid


def _messages(items, tags: list[Tag]) -> list[Message]:
    by_id = {t.id: t for t in tags}
    out: list[Message] = []
    names = set()
    for i, m in enumerate(items):
        ctx = f"messages[{i}]"
        name = str(m.get("name", f"msg{i + 1}"))
        if name in names:
            raise ScenarioError(f"{ctx}: 메시지 이름 {name!r}이 중복되었습니다.")
        names.add(name)
        if "from" not in m or "to" not in m:
            raise ScenarioError(f"{ctx}: from, to 필드가 필요합니다.")
        src = _endpoint(m["from"], ctx + ".from", by_id)
        dst = _endpoint(m["to"], ctx + ".to", by_id)
        if src == dst:
            raise ScenarioError(f"{ctx}: from과 to가 같습니다.")
        if src in MSG_ENDPOINTS and dst in MSG_ENDPOINTS:
            raise ScenarioError(f"{ctx}: 지상국에서 지상국으로 가는 메시지는 드론이 운반할 것이 없습니다.")
        nbytes = int(m.get("bytes", 64))
        if nbytes <= 0:
            raise ScenarioError(f"{ctx}: bytes는 양수여야 합니다.")
        out.append(Message(name, src, dst, nbytes, int(m.get("priority", 1))))
    return out


def load_scenario(path: Union[str, Path]) -> Scenario:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ScenarioError(f"시나리오 파일을 찾을 수 없습니다: {p}")
    except json.JSONDecodeError as e:
        raise ScenarioError(f"JSON 문법 오류 ({p}): {e}")
    return parse_scenario(data)

"""미션 산출물에서 훈련 타임라인 생성."""
from __future__ import annotations

from typing import Any


def build_timeline(mission: dict[str, Any], start_offset_s: float = 0.0) -> dict[str, Any]:
    waypoints = mission.get("waypoints") or []
    events: list[dict[str, Any]] = [
        {"t_s": start_offset_s, "kind": "canary", "label": "카나리아 하트비트 정상", "phase": "online"},
        {"t_s": start_offset_s + 2, "kind": "emp", "label": "LINK LOST · 하트비트 중단", "phase": "offline"},
        {"t_s": start_offset_s + 5, "kind": "quiet", "label": "정숙 시간 확인", "phase": "offline"},
        {"t_s": start_offset_s + 7, "kind": "nest", "label": "둥지 개방 · 플랜 카드 판독", "phase": "offline"},
    ]
    elapsed = float(start_offset_s) + 8
    previous: dict[str, Any] | None = None
    for waypoint in waypoints:
        if previous:
            dx = float(waypoint["x_m"]) - float(previous["x_m"])
            dy = float(waypoint["y_m"]) - float(previous["y_m"])
            distance = (dx * dx + dy * dy) ** 0.5
            speed = max(0.01, float(waypoint.get("speed_mps") or 0.5))
            elapsed += distance / speed
        event = {
            "t_s": round(elapsed, 1),
            "waypoint_id": waypoint.get("id"),
            "kind": waypoint.get("kind"),
            "label": waypoint.get("label") or waypoint.get("kind"),
            "position": [waypoint.get("x_m"), waypoint.get("y_m"), waypoint.get("z_m")],
            "link_expected": waypoint.get("link_expected", True),
            "phase": "mission",
            "actions": [],
        }
        if not event["link_expected"]:
            event["actions"].append("통신 두절 절차 확인")
        if waypoint.get("kind") in {"fix", "comm", "read"}:
            event["actions"].append(f"태그 {waypoint.get('tag_id')} 식별 확인")
        if waypoint.get("kind") in {"comm", "read"}:
            event["actions"].append("메시지 송수신 확인")
        if waypoint.get("kind") == "land":
            event["actions"].append("착륙 및 안전 확인")
        events.append(event)
        elapsed += float(waypoint.get("dwell_s") or 0)
        previous = waypoint

    base_points = [[w.get("x_m"), w.get("y_m")] for w in waypoints]
    samples = [
        {
            "id": index,
            "label": "성공" if index < 3 else "경계 사례",
            "success": index < 3,
            "points": [
                [round(float(point[0]) + (index - 1.5) * 0.03, 3), round(float(point[1]) - (index - 1.5) * 0.02, 3)]
                for point in base_points
            ],
        }
        for index in range(4)
    ]
    return {
        "schema": "d4d.drill/1",
        "scenario": mission.get("scenario"),
        "duration_s": round(elapsed, 1),
        "events": events,
        "samples": samples,
        "plan_card_id": mission.get("plan_card_id", -1),
        "result": {
            "verified": mission.get("verified", False),
            "delivery_rate": (mission.get("network") or {}).get("delivery_rate"),
            "exposure_s": round(max(0.0, elapsed - (start_offset_s + 7)), 1),
        },
        "checklist": [
            "비행 전 승인 상태와 시나리오 해시 확인",
            "지오펜스·비상 착륙 지점 확인",
            "UART 명령 타임아웃과 수동 중단 시험",
            "훈련 종료 후 로그와 실제 타임라인 비교",
        ],
    }

"""산출물 생성: mission.json, mission_data.h, plan.svg, report.md, agent_trace.json."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from .agent import AgentResult
from .model import Waypoint
from .network import endpoint_label, messages_at_stop

KIND_ENUM = {"takeoff": 0, "goto": 1, "fix": 2, "hover": 3, "land": 4, "comm": 5, "read": 6}
ROLE_ENUM = {"marker": 0, "mailbox": 1, "ble": 2}
ROLE_KO = {"marker": "종이 태그", "mailbox": "광학 메일박스", "ble": "근접 BLE"}
EP_START, EP_GOAL = -1, -2  # C 헤더에서 지상국 끝점 표기


def _r(v, n=3):
    return None if v is None else round(float(v), n)


def _ep_c(e) -> int:
    return EP_START if e == "start" else EP_GOAL if e == "goal" else int(e)


def mission_dict(res: AgentResult, plan_card_id: int = -1) -> dict:
    sc, plan, p, mc = res.scenario, res.plan, res.params, res.mc
    d = sc.drone
    wps = []
    for w in plan.waypoints:
        entry = {
            "id": w.idx,
            "kind": w.kind,
            "x_m": _r(w.x),
            "y_m": _r(w.y),
            "z_m": _r(w.z),
            "speed_mps": _r(w.speed),
            "tag_id": w.tag_id,
            "dwell_s": _r(w.dwell_s, 2),
            "link_expected": w.link_expected,
            "expected_sigma_m": _r(w.expected_sigma_m),
            "tolerance_m": _r(w.tolerance_m),
            "abort_land_at": [_r(w.abort_land_at[0]), _r(w.abort_land_at[1])] if w.abort_land_at else None,
            "label": w.label,
        }
        if w.kind in ("comm", "read"):
            pick, drop = messages_at_stop(sc, w.segment)
            entry["comm"] = {
                "medium": "ble" if w.kind == "comm" else "optical",
                "altitude_m": _r(d.comm_altitude_m, 2),
                "xfer_bytes": w.xfer_bytes,
                "pickup": [m.name for m in pick],
                "deliver": [m.name for m in drop],
            }
        wps.append(entry)
    nodes = [
        {
            "id": t.id,
            "role": t.role,
            "x_m": _r(t.x),
            "y_m": _r(t.y),
            "ble_range_m": _r(d.node_range_m(t), 2) if t.role == "ble" else None,
            "payload_bytes": t.payload_bytes,
            "install_required": t.suggested,
            "role_change_suggested": t.role_suggested,
        }
        for t in sc.nodes
    ]
    msgs = [
        {
            "name": m.name,
            "from": m.src,
            "to": m.dst,
            "bytes": m.bytes,
            "priority": m.priority,
            "delivery_rate": _r(mc.delivery_by_msg.get(m.name), 3) if mc else None,
        }
        for m in sc.messages
    ]
    return {
        "schema": "d4d.mission/1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenario": sc.name,
        "plan_card_id": plan_card_id,
        "frame": "local ENU, origin=필드 좌하단, x=동, y=북, 단위 m",
        "link_policy": sc.link_policy,
        "verified": res.success,
        "uart": {
            "cmd_rate_hz": d.cmd_rate_hz,
            "cmd_timeout_ms": d.cmd_timeout_ms,
            "note": "FC는 cmd_timeout_ms 동안 명령이 없으면 자동 미션을 중단해야 한다(Notion Failsafe 설계).",
        },
        "executor_rules": {
            "fix_search_radius_m": _r(d.tag_detect_radius_m),
            "on_fix_failed": "abort_land_at 로 이동 후 착륙(없으면 제자리 착륙)",
            "abort_if_sigma_exceeds_m": _r(d.tag_detect_radius_m / p.z),
            "battery_abort_frac": d.reserve_frac,
        },
        "estimate": {
            "total_time_s": _r(plan.total_time_s, 1),
            "total_length_m": _r(plan.total_length_m, 1),
            "jam_length_m": _r(plan.jam_len_m, 1),
            "time_budget_s": _r(res.time_budget_s, 1),
        },
        "params": {"margin_m": p.margin_m, "z": p.z, "jam_margin_m": p.jam_margin_m, "min_delivery": p.min_delivery},
        "tags": [
            {
                "id": t.id,
                "x_m": _r(t.x),
                "y_m": _r(t.y),
                "role": t.role,
                "install_required": t.suggested,
                "used": t.id in plan.tags_used,
            }
            for t in sc.tags
        ],
        "network": {
            "model": {
                "ble_range_m": d.ble_range_m,
                "ble_link_prob": d.ble_link_prob,
                "ble_jam_shrink": d.ble_jam_shrink,
                "ble_rate_Bps": d.ble_rate_Bps,
                "optical_rate_Bps": d.optical_rate_Bps,
                "read_code_prob": d.read_code_prob,
                "comm_altitude_m": d.comm_altitude_m,
                "comm_dwell_s": d.comm_dwell_s,
                "note": "링크 버짓 추정에 기반한 가정 모델. BLE 성공률 vs 거리/간섭, 디스플레이 코드 판독률을 실측해 보정할 것.",
            },
            "min_delivery": p.min_delivery,
            "delivery_rate": _r(mc.delivery_rate, 3) if mc else None,
            "nodes": nodes,
            "messages": msgs,
        },
        "waypoints": wps,
    }


def write_header(res: AgentResult, path: Path, plan_card_id: int = -1) -> None:
    plan, sc = res.plan, res.scenario
    used = [t for t in sc.tags if t.id in plan.tags_used]
    L = [
        "// 자동 생성 파일 - 직접 수정하지 마세요 (d4d_planner)",
        f"// scenario: {sc.name}   verified: {str(res.success).lower()}",
        "#pragma once",
        "#include <stdint.h>",
        "",
        "typedef enum { WP_TAKEOFF = 0, WP_GOTO = 1, WP_FIX = 2, WP_HOVER = 3, WP_LAND = 4,",
        "               WP_COMM = 5,    // 노드 위에서 근접 BLE 교신 (+위치 보정)",
        "               WP_READ = 6     // 노드 디스플레이 코드 판독, 광학 교신 (+위치 보정)",
        "             } mission_wp_kind_t;",
        "typedef enum { NODE_MARKER = 0, NODE_MAILBOX = 1, NODE_BLE = 2 } mission_node_role_t;",
        "",
        "typedef struct {",
        "  float x_m, y_m, z_m;     // local ENU",
        "  float speed_mps;",
        "  uint8_t kind;            // mission_wp_kind_t",
        "  int16_t tag_id;          // WP_FIX/WP_COMM/WP_READ 일 때 유효, 아니면 -1",
        "  float dwell_s;           // COMM/READ: 하강+교신+상승 예상 시간",
        "  float tolerance_m;",
        "  float expected_sigma_m;  // 도착 시 예상 1-sigma 오차",
        "  uint8_t link_expected;   // 0: 재밍 구역(RC 두절 예상)",
        "  float abort_x_m, abort_y_m; // 비상 착륙 지점(NAN이면 제자리)",
        "  uint16_t xfer_bytes;     // COMM/READ: 주고받을 바이트",
        "} mission_wp_t;",
        "",
        "typedef struct { int16_t id; float x_m, y_m; uint8_t role; float ble_range_m; } mission_tag_t;",
        "// src/dst: 노드(태그) id, MISSION_EP_START(-1)=출발 지상국, MISSION_EP_GOAL(-2)=착륙 지상국",
        "typedef struct { int16_t src, dst; uint16_t bytes; uint8_t priority; } mission_msg_t;",
        "",
        f"#define MISSION_LINK_POLICY_CONTINUE {1 if sc.link_policy == 'continue_mission' else 0}",
        f"#define MISSION_CMD_RATE_HZ {sc.drone.cmd_rate_hz:.1f}f",
        f"#define MISSION_CMD_TIMEOUT_MS {sc.drone.cmd_timeout_ms:.0f}",
        f"#define MISSION_TAG_DETECT_RADIUS_M {sc.drone.tag_detect_radius_m:.2f}f",
        f"#define MISSION_COMM_ALTITUDE_M {sc.drone.comm_altitude_m:.2f}f",
        f"#define MISSION_COMM_TRIES 3",
        f"#define MISSION_EP_START {EP_START}",
        f"#define MISSION_EP_GOAL {EP_GOAL}",
        f"#define MISSION_PLAN_CARD_ID {plan_card_id}",
        f"#define MISSION_WP_COUNT {len(plan.waypoints)}",
        f"#define MISSION_TAG_COUNT {len(used)}",
        f"#define MISSION_MSG_COUNT {len(sc.messages)}",
        "",
        "static const mission_wp_t MISSION_WPS[MISSION_WP_COUNT] = {",
    ]
    for w in plan.waypoints:
        ax, ay = (w.abort_land_at if w.abort_land_at else (None, None))
        a = f"{ax:.3f}f, {ay:.3f}f" if ax is not None else "NAN, NAN"
        L.append(
            f"  {{ {w.x:.3f}f, {w.y:.3f}f, {w.z:.3f}f, {w.speed:.3f}f, {KIND_ENUM[w.kind]}, "
            f"{w.tag_id if w.tag_id is not None else -1}, {w.dwell_s:.2f}f, {w.tolerance_m:.2f}f, "
            f"{w.expected_sigma_m:.3f}f, {1 if w.link_expected else 0}, {a}, {w.xfer_bytes} }},  // {w.idx}: {w.kind} {w.label}".rstrip()
        )
    L.append("};")
    L.append("")
    L.append("static const mission_tag_t MISSION_TAGS[MISSION_TAG_COUNT > 0 ? MISSION_TAG_COUNT : 1] = {")
    for t in used:
        rng = sc.drone.node_range_m(t) if t.role == "ble" else 0.0
        L.append(f"  {{ {t.id}, {t.x:.3f}f, {t.y:.3f}f, {ROLE_ENUM[t.role]}, {rng:.2f}f }},  // {ROLE_KO[t.role]}")
    if not used:
        L.append("  { -1, 0.0f, 0.0f, 0, 0.0f },")
    L.append("};")
    L.append("")
    L.append("static const mission_msg_t MISSION_MSGS[MISSION_MSG_COUNT > 0 ? MISSION_MSG_COUNT : 1] = {")
    for m in sc.messages:
        L.append(f"  {{ {_ep_c(m.src)}, {_ep_c(m.dst)}, {m.bytes}, {m.priority} }},  // {m.name}")
    if not sc.messages:
        L.append("  { 0, 0, 0, 0 },")
    L.append("};")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------- SVG


def write_svg(res: AgentResult, path: Path, px_per_m: float = 20.0) -> None:
    sc, plan, mc = res.scenario, res.plan, res.mc
    W, H = sc.field.width_m, sc.field.height_m
    m = 30
    wpx, hpx = W * px_per_m + 2 * m, H * px_per_m + 2 * m + 60 + (20 if sc.nodes else 0)

    def X(x):
        return m + x * px_per_m

    def Y(y):
        return m + (H - y) * px_per_m

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{wpx:.0f}" height="{hpx:.0f}" font-family="sans-serif" font-size="11">',
        f'<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<rect x="{X(0)}" y="{Y(H)}" width="{W * px_per_m}" height="{H * px_per_m}" fill="#fff" stroke="#333"/>',
    ]
    style = {
        "obstacle": ("#555", 0.9),
        "keepout": ("#c0392b", 0.35),
        "jam": ("#f39c12", 0.35),
        "low_flow": ("#3498db", 0.22),
    }
    for z in sc.zones:
        col, op = style[z.kind]
        s = z.shape
        if s.kind == "rect":
            x0, y0, x1, y1 = s.pts
            out.append(f'<rect x="{X(x0)}" y="{Y(y1)}" width="{(x1 - x0) * px_per_m}" height="{(y1 - y0) * px_per_m}" fill="{col}" fill-opacity="{op}"/>')
        elif s.kind == "circle":
            cx, cy, r = s.pts
            out.append(f'<circle cx="{X(cx)}" cy="{Y(cy)}" r="{r * px_per_m}" fill="{col}" fill-opacity="{op}"/>')
        else:
            pts = " ".join(f"{X(a)},{Y(b)}" for a, b in s.pts)
            out.append(f'<polygon points="{pts}" fill="{col}" fill-opacity="{op}"/>')
    if mc:
        for tr in mc.traces:
            pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in tr)
            out.append(f'<polyline points="{pts}" fill="none" stroke="#8e44ad" stroke-opacity="0.18" stroke-width="1"/>')
    if plan and plan.ok:
        pts = " ".join(f"{X(w.x):.1f},{Y(w.y):.1f}" for w in plan.waypoints)
        out.append(f'<polyline points="{pts}" fill="none" stroke="#27ae60" stroke-width="2.5"/>')
    node_col = {"ble": "#8e24aa", "mailbox": "#00838f"}
    for t in sc.tags:
        used = plan is not None and t.id in plan.tags_used
        col = "#2c3e50" if not t.suggested else "#e67e22"
        dash = ' stroke-dasharray="3,2"' if t.suggested else ""
        out.append(f'<circle cx="{X(t.x)}" cy="{Y(t.y)}" r="{sc.drone.tag_detect_radius_m * px_per_m}" fill="none" stroke="{col}" stroke-opacity="{0.5 if used else 0.2}"{dash}/>')
        if t.is_node:
            nc = node_col[t.role]
            if t.role == "ble":
                # 재밍 없는 곳 기준 BLE 범위(점선). 재밍 안에서는 이보다 줄어든다.
                out.append(f'<circle cx="{X(t.x)}" cy="{Y(t.y)}" r="{sc.drone.node_range_m(t) * px_per_m}" fill="none" stroke="{nc}" stroke-opacity="0.45" stroke-dasharray="5,3"/>')
            out.append(f'<polygon points="{X(t.x)},{Y(t.y) - 8} {X(t.x) + 8},{Y(t.y)} {X(t.x)},{Y(t.y) + 8} {X(t.x) - 8},{Y(t.y)}" fill="{nc}" stroke="#fff"/>')
            tag_lbl = f"N{t.id}" + ("*" if t.role_suggested else "")
            out.append(f'<text x="{X(t.x) + 8}" y="{Y(t.y) - 8}" fill="{nc}" font-weight="bold">{tag_lbl}</text>')
        else:
            out.append(f'<rect x="{X(t.x) - 4}" y="{Y(t.y) - 4}" width="8" height="8" fill="{col}"/>')
            out.append(f'<text x="{X(t.x) + 6}" y="{Y(t.y) - 6}" fill="{col}">T{t.id}</text>')
    if plan and plan.ok:
        for w in plan.waypoints:
            c = {"takeoff": "#2980b9", "goto": "#27ae60", "fix": "#16a085", "hover": "#d35400", "land": "#c0392b",
                 "comm": "#8e24aa", "read": "#00838f"}[w.kind]
            out.append(f'<circle cx="{X(w.x)}" cy="{Y(w.y)}" r="{4 if w.kind == "goto" else 6}" fill="{c}" stroke="#fff"/>')
            if w.kind != "goto":
                out.append(f'<text x="{X(w.x) + 8}" y="{Y(w.y) + 14}" fill="{c}" font-weight="bold">{w.idx}</text>')
    ly = Y(0) + 24
    items = [("#555", "장애물"), ("#c0392b", "금지구역"), ("#f39c12", "재밍/EMP"), ("#3498db", "광류 저하"),
             ("#27ae60", "계획 경로"), ("#8e44ad", "시뮬 궤적(오차 주입)"), ("#e67e22", "설치 필요 태그")]
    if sc.nodes:
        items += [("#8e24aa", "BLE 노드(점선=범위)"), ("#00838f", "광학 메일박스 노드")]
    lx = m
    for col, name in items:
        if lx + 22 + len(name) * 11 > wpx - m:
            lx, ly = m, ly + 18
        out.append(f'<rect x="{lx}" y="{ly}" width="12" height="12" fill="{col}"/><text x="{lx + 16}" y="{ly + 10}">{name}</text>')
        lx += 22 + len(name) * 11
    title = f"{sc.name}  |  policy={sc.link_policy}  |  {'검증 통과' if res.success else '검증 미통과'}"
    out.append(f'<text x="{m}" y="{m - 10}" font-size="13" font-weight="bold">{title}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- 리포트


def _report_network(L: list[str], res: AgentResult) -> None:
    sc, plan, mc, p, g = res.scenario, res.plan, res.mc, res.params, res.grid
    d = sc.drone
    L.append("## 3-1. 네트워크 (스파이더 코드: 노드 교신과 메시지 운반)\n")
    L.append(
        "드론이 노드(코드가 붙은 지상 ESP32) 위에서 위치를 보정하면서 메시지를 싣고 내려놓는 데이터 뮬 방식입니다. "
        "BLE 노드는 재밍 강도에 따라 유효 거리가 줄고, 광학 메일박스는 재밍과 무관합니다.\n"
    )
    L.append("| 노드 | 역할 | x | y | 재밍 | 유효 BLE 거리(m) | 교신 실패(회) | 비고 |")
    L.append("|------|------|---|---|------|------------------|---------------|------|")
    for t in sc.nodes:
        jam = g.jam[g.cell(t.x, t.y)] if g else 0.0
        if t.role == "ble":
            r_eff = d.node_range_m(t) * max(0.05, 1.0 - d.ble_jam_shrink * jam)
            r_txt = f"{r_eff:.2f} (기준 {d.node_range_m(t):.1f})"
        else:
            r_txt = "-"
        fails = mc.comm_fail_by_node.get(t.id, 0) if mc else 0
        note = []
        if t.role_suggested:
            note.append("에이전트 제안: BLE -> 광학 메일박스로 전환")
        if t.suggested:
            note.append("설치 필요")
        L.append(f"| {t.id} | {ROLE_KO[t.role]} | {t.x:.2f} | {t.y:.2f} | {jam:.1f} | {r_txt} | {fails} | {'; '.join(note)} |")
    L.append("")
    if sc.messages:
        L.append(f"| 메시지 | 출발 | 도착 | 크기(B) | 우선순위 | 전달률 (기준 {p.min_delivery:.0%}) |")
        L.append("|--------|------|------|---------|----------|---------------------|")
        for m in sc.messages:
            rate = mc.delivery_by_msg.get(m.name) if mc else None
            r_txt = f"**{rate:.1%}**" if rate is not None else "-"
            L.append(f"| {m.name} | {endpoint_label(m.src)} | {endpoint_label(m.dst)} | {m.bytes} | {m.priority} | {r_txt} |")
        L.append("")
    if plan and plan.ok:
        stops = [w for w in plan.waypoints if w.kind in ("comm", "read")]
        if stops:
            L.append("교신 순서: " + " -> ".join(f"#{w.idx} 노드 {w.tag_id}({'BLE' if w.kind == 'comm' else '광학'}, {w.xfer_bytes} B, {w.dwell_s:.0f} s)" for w in stops))
            L.append("")
    L.append(
        f"- 교신 모델(가정): BLE 범위 {d.ble_range_m} m, 재밍 1.0에서 범위 {1 - d.ble_jam_shrink:.0%}로 축소, 1회 성공 {d.ble_link_prob:.0%}, "
        f"{d.ble_rate_Bps / 1000:.1f} KB/s; 광학 판독 1회 성공 {d.read_code_prob:.0%}, {d.optical_rate_Bps:.0f} B/s; 교신 고도 {d.comm_altitude_m} m, 재시도 3회."
    )
    L.append("- 전달률은 같은 메시지가 몬테카를로 전체 회차 중 목적지에 도착한 비율입니다. 교신 실패는 비행을 중단시키지 않고 전달률에만 반영됩니다.")
    L.append("")


def write_report(res: AgentResult, path: Path) -> None:
    sc, plan, mc, a, p = res.scenario, res.plan, res.mc, res.analysis, res.params
    d = sc.drone
    L: list[str] = []
    L.append(f"# 미션 계획 리포트: {sc.name}\n")
    verdict = "통과" if res.success else "미통과"
    L.append(f"- 결과: **검증 {verdict}**  /  링크 정책: `{sc.link_policy}`")
    L.append(f"- 생성: {datetime.now():%Y-%m-%d %H:%M}\n")

    if res.issues or a.blocking:
        L.append("## 주의/차단 사항\n")
        for s in a.blocking + res.issues:
            L.append(f"- {s}")
        L.append("")

    L.append("## 1. 전장 분석\n")
    L.append(f"- 격자 {a.cells}셀, 지도상 통과 영역 {a.free_frac:.0%}, 재밍 구역 {a.jam_frac:.0%}, 광류 저하 구역 {a.low_flow_frac:.0%}")
    L.append(f"- 태그 보정 후 다음 태그까지 허용 거리: 정상 구역 약 **{a.nominal_leg_m:.1f} m**, 광류 저하 구역 약 {a.low_flow_leg_m:.1f} m")
    L.append("- 목표 도달성: " + ", ".join(f"{n}={'가능' if ok else '불가'}" for n, ok in a.reachable))
    for s in a.tag_notes + a.network_notes + a.warnings:
        L.append(f"- {s}")
    L.append("")

    if plan and plan.ok:
        L.append("## 2. 계획된 코스\n")
        L.append(f"- 총 길이 {plan.total_length_m:.1f} m, 예상 {plan.total_time_s:.0f} s (배터리 예산 {res.time_budget_s:.0f} s)")
        L.append(f"- 재밍 구역 통과 길이 {plan.jam_len_m:.1f} m" + (" (이 구간은 조종기로 회수할 수 없음)" if plan.jam_len_m > 0 else ""))
        L.append(f"- 사용 태그: {', '.join('T' + str(i) for i in plan.tags_used) or '없음'}\n")
        L.append("| # | 종류 | x | y | 속도 | 태그 | 예상 σ(m) | 링크 | 비고 |")
        L.append("|---|------|---|---|------|------|-----------|------|------|")
        for w in plan.waypoints:
            L.append(
                f"| {w.idx} | {w.kind} | {w.x:.2f} | {w.y:.2f} | {w.speed:.2f} | {w.tag_id if w.tag_id is not None else ''} "
                f"| {w.expected_sigma_m:.3f} | {'정상' if w.link_expected else '재밍'} | {w.label} |"
            )
        L.append("")

        L.append("## 3. 시뮬레이션 검증 (오차 주입 몬테카를로)\n")
        if mc:
            L.append(f"- {mc.runs}회 중 성공 **{mc.success_rate:.1%}** (기준 {p.min_success:.0%}), 충돌 {mc.collision_rate:.1%} (기준 ≤ {p.max_collision:.0%})")
            L.append(f"- 평균 소요 {mc.mean_time_s:.0f} s, 95% {mc.p95_time_s:.0f} s, 착륙 최악 오차 {mc.worst_final_err_m:.2f} m")
            L.append("- 결과 분포: " + ", ".join(f"{k}={v}" for k, v in sorted(mc.counts.items())))
            if mc.fix_miss_by_tag:
                L.append("- 태그 인식 실패 다발: " + ", ".join(f"T{k}({v}회)" for k, v in mc.fix_miss_by_tag.items()))
        L.append("")

    if sc.nodes or sc.messages:
        _report_network(L, res)

    if res.suggested_tags:
        L.append("## 4. 설치가 필요한 태그 (에이전트 제안)\n")
        L.append("계획이 성립하려면 아래 위치에 AprilTag를 설치해야 합니다. 설치가 불가능하면 경로/목표를 조정하세요.\n")
        L.append("| id | x(m) | y(m) | 계획에서 사용 |")
        L.append("|----|------|------|---------------|")
        for t in res.suggested_tags:
            L.append(f"| {t.id} | {t.x:.2f} | {t.y:.2f} | {'예' if plan and t.id in plan.tags_used else '아니오'} |")
        L.append("")

    L.append("## 5. 에이전트 판단 기록\n")
    for s in res.trace:
        L.append(f"{s.it}. `{s.tool}` - {s.summary}")
    L.append("")

    L.append("## 6. 가정과 한계 (반드시 확인)\n")
    L.append(
        f"- 드리프트 모델은 **가정치**입니다: 광류 스케일 σ={d.flow_scale_sigma:.0%}, 요 드리프트 σ={d.yaw_drift_sigma} rad, "
        f"랜덤워크 {d.noise_per_sqrt_m} m/√m, 태그 인식 반경 {d.tag_detect_radius_m} m, 인식 확률 {d.tag_detect_prob:.0%}. 실비행 로그로 보정하세요."
    )
    L.append("- 2D 계획입니다. 장애물은 비행 고도(" + f"{d.altitude_m} m)보다 높다고 가정합니다.")
    if sc.nodes:
        L.append(
            "- 노드 교신 모델(BLE 범위/재밍 축소율/판독률)은 **링크 버짓 추정에 기반한 가정**입니다. "
            "BLE 패킷 성공률 vs 거리(0.5/1/2/5 m, Wi-Fi 간섭 포함)와 디스플레이 코드 판독률을 실측해 보정하기 전까지 전달률은 설계 비교용으로만 쓰세요."
        )
    L.append("- 이 도구는 **출발 전(지상)** 에 코스를 만듭니다. 비행 중에는 통신 없이 mission.json / mission_data.h 만으로 동작합니다.")
    if sc.link_policy == "continue_mission":
        L.append("- `continue_mission` 은 링크 두절에도 자동 미션을 계속합니다. Notion 초기 시험 설계(조종기 끊김 시 자동 미션 중단)와 다르므로, "
                 "실외/인원 근처 사용 전 FC 펌웨어 failsafe, 지오펜스, 물리적 차단 수단을 별도로 확보해야 합니다.")
    else:
        L.append("- `abort` 정책: 재밍 구역은 통과하지 않도록 계획했습니다(Notion 초기 시험 설계와 동일).")
    L.append("- UART 명령 타임아웃 감시와 수동/자동 전환은 FC 펌웨어가 지원해야 합니다(Notion 문서의 확인 항목).")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def write_all(res: AgentResult, out_dir: Path, plan_card_id: int = -1) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    trace = [{"it": s.it, "tool": s.tool, "summary": s.summary, "data": s.data} for s in res.trace]
    (out_dir / "agent_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    files.append(out_dir / "agent_trace.json")
    if res.plan and res.plan.ok:
        mp = out_dir / "mission.json"
        mp.write_text(json.dumps(mission_dict(res, plan_card_id), ensure_ascii=False, indent=2), encoding="utf-8")
        files.append(mp)
        hp = out_dir / "mission_data.h"
        write_header(res, hp, plan_card_id)
        files.append(hp)
    sp = out_dir / "plan.svg"
    write_svg(res, sp)
    files.append(sp)
    rp = out_dir / "report.md"
    write_report(res, rp)
    files.append(rp)
    return files

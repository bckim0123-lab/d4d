"""미션 계획 에이전트: 분석 -> 계획 -> 검증 -> 진단/수정 루프.

에이전트는 도구(분석, 경로계획, 태그제안, 몬테카를로 검증)를 호출하고,
검증 실패 원인에 따라 파라미터를 조정해 재계획한다. 모든 판단은 결정론적이며
'agent_trace'로 기록되어 사후 검토할 수 있다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, Optional

from .analysis import Analysis, analyze
from .grid import Grid
from .model import MissionPlan, PlanParams, Scenario
from .planner import PlanContext, plan_mission
from .sim import MCResult, simulate
from .tagplan import SUGGEST_ID_BASE, filter_new, suggest_tags

MARGIN_MIN, MARGIN_MAX = 0.05, 1.0
Z_MAX = 4.0


@dataclass
class AgentConfig:
    max_iters: int = 8
    suggest_tags: bool = True


@dataclass
class TraceStep:
    it: int
    tool: str
    summary: str
    data: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    success: bool
    scenario: Scenario
    params: PlanParams
    analysis: Analysis
    plan: Optional[MissionPlan]
    mc: Optional[MCResult]
    grid: Optional[Grid]
    trace: list[TraceStep]
    suggested_tags: list = field(default_factory=list)
    time_budget_s: float = 0.0
    issues: list[str] = field(default_factory=list)
    suggested_roles: list = field(default_factory=list)  # 에이전트가 역할 변경을 제안한 노드


TraceCallback = Callable[[TraceStep], None]


class _TraceLog(list[TraceStep]):
    """TraceStep을 기록하면서 UI/SSE 구독자에게 즉시 전달한다."""

    def __init__(self, callback: TraceCallback | None = None):
        super().__init__()
        self.callback = callback

    def append(self, step: TraceStep) -> None:
        super().append(step)
        if self.callback:
            try:
                self.callback(step)
            except Exception:
                # 관측 UI의 실패가 결정론적 계획을 중단시키면 안 된다.
                pass


def run_agent(
    scenario: Scenario,
    params: PlanParams | None = None,
    cfg: AgentConfig | None = None,
    on_step: TraceCallback | None = None,
) -> AgentResult:
    cfg = cfg or AgentConfig()
    params = copy.copy(params or PlanParams())
    sc = scenario.clone()
    trace: list[TraceStep] = _TraceLog(on_step)
    d = sc.drone
    budget = d.endurance_s * (1.0 - d.reserve_frac)

    grid = Grid(sc, params)
    analysis = analyze(sc, grid, params)
    trace.append(
        TraceStep(
            0,
            "analyze",
            f"지도상 통과 영역 {analysis.free_frac:.0%}, 재밍 {analysis.jam_frac:.0%}, 광류저하 {analysis.low_flow_frac:.0%}, "
            f"태그 간 허용거리 {analysis.nominal_leg_m:.1f} m",
            analysis.to_dict(),
        )
    )

    best: tuple[float, MissionPlan, MCResult, Scenario, PlanParams] | None = None
    last_plan: Optional[MissionPlan] = None
    issues: list[str] = []
    success = False

    if analysis.blocking:
        # 마진을 줄여 해결 가능한 경우만 에이전트가 시도, 그 외에는 즉시 보고
        trace.append(TraceStep(0, "diagnose", "계획 전 차단 요인: " + " / ".join(analysis.blocking)))

    max_iters = cfg.max_iters
    if analysis.needs_jam_crossing:
        # 정책 문제는 파라미터 조정으로 풀리지 않는다 -> 반복 없이 보고
        trace.append(TraceStep(0, "stop", "재밍 구역 통과가 필수인데 정책이 abort 입니다. 자동 수정 불가 -> 사용자 결정 필요"))
        max_iters = 0

    for it in range(1, max_iters + 1):
        grid.set_params(params)
        ctx = PlanContext(sc, grid, params)
        plan = plan_mission(ctx)
        last_plan = plan
        if not plan.ok:
            fd = plan.failed_detail
            trace.append(
                TraceStep(
                    it,
                    "plan",
                    f"실패: 구간 {plan.failed_segment}('{fd.get('objective')}'), 원인={plan.reason}",
                    {"reason": plan.reason, "rejections": len(fd.get("rejections", []))},
                )
            )
            if plan.reason in ("no_path", "endpoint_blocked"):
                if params.margin_m > MARGIN_MIN + 1e-9:
                    params.margin_m = round(max(MARGIN_MIN, params.margin_m - 0.1), 3)
                    trace.append(TraceStep(it, "revise", f"경로 없음 -> 안전 마진을 {params.margin_m:.2f} m 로 축소 후 재시도"))
                    continue
                issues.append("마진을 최소로 줄여도 비행 가능 경로가 없습니다. 장애물 배치/재밍 정책을 확인하세요.")
                break
            if plan.reason in ("drift", "clearance") and cfg.suggest_tags:
                ids = [t.id for t in sc.tags if t.id >= SUGGEST_ID_BASE]
                next_id = max(ids) + 1 if ids else SUGGEST_ID_BASE
                new = suggest_tags(ctx, fd["from"], fd["e0"], fd["to"], next_id, fd["tolerance_m"])
                new = filter_new(ctx.tags, new, 0.4)
                if not new:
                    issues.append(
                        "태그를 더 배치해도 오차 예산/통로 여유를 만족시킬 수 없습니다(통로가 너무 좁거나 파라미터 한계)."
                    )
                    break
                sc.tags.extend(new)
                trace.append(
                    TraceStep(
                        it,
                        "suggest_tags",
                        f"오차 예산 초과 구간에 태그 {len(new)}개 추가 제안",
                        {"tags": [(t.id, round(t.x, 2), round(t.y, 2)) for t in new]},
                    )
                )
                continue
            issues.append(
                f"구간 '{fd.get('objective')}'에서 드리프트 예산을 만족하는 태그 경유 경로를 찾지 못했습니다. "
                "태그를 추가 설치하거나 드론 사양(오차 파라미터)을 재측정하세요."
            )
            break

        mc = simulate(plan, sc, grid, params)
        time_ok = plan.total_time_s <= budget
        passes = mc.passes(params) and time_ok
        net = f", 전달률 {mc.delivery_rate:.1%}" if sc.messages else ""
        trace.append(
            TraceStep(
                it,
                "verify",
                f"성공률 {mc.success_rate:.1%}, 충돌 {mc.collision_rate:.1%}{net}, 예상 {plan.total_time_s:.0f}s "
                f"(예산 {budget:.0f}s) -> {'통과' if passes else '미달'}",
                {
                    "counts": mc.counts,
                    "tags_used": plan.tags_used,
                    "length_m": round(plan.total_length_m, 1),
                    "delivery": {k: round(v, 3) for k, v in mc.delivery_by_msg.items()},
                    "comm_fail_by_node": mc.comm_fail_by_node,
                },
            )
        )
        score = mc.success_rate + 0.5 * mc.delivery_rate - (0.0 if time_ok else 1.0)
        if best is None or score > best[0]:
            best = (score, plan, mc, copy.deepcopy(sc), copy.copy(params))
        if passes:
            success = True
            break

        if not time_ok:
            issues.append(
                f"예상 비행시간 {plan.total_time_s:.0f}s 가 배터리 예산 {budget:.0f}s 를 넘습니다. 목표 축소/속도 증가/배터리 확대가 필요합니다."
            )
            break
        dom = mc.dominant_failure()
        flight_ok = mc.success_rate >= params.min_success and mc.collision_rate <= params.max_collision
        if flight_ok and mc.delivery_rate < params.min_delivery:
            # 비행은 되는데 교신이 안 된다 -> 가장 많이 실패한 노드를 본다
            nid = mc.worst_node()
            node = sc.tag(nid) if nid is not None else None
            jam = grid.jam[grid.cell(node.x, node.y)] if node else 0.0
            if node is not None and node.role == "ble" and jam > 0:
                node.role = "mailbox"
                node.role_suggested = True
                trace.append(
                    TraceStep(
                        it,
                        "revise_node",
                        f"노드 {nid} 근접 BLE 교신 실패 {mc.comm_fail_by_node[nid]}회(재밍 {jam:.1f}) -> "
                        "광학 메일박스(디스플레이 코드)로 전환 제안 후 재검증",
                        {"node": nid, "from": "ble", "to": "mailbox", "jam": jam},
                    )
                )
                continue
            low = [k for k, v in mc.delivery_by_msg.items() if v < params.min_delivery]
            if node is not None and node.role == "mailbox":
                issues.append(
                    f"노드 {nid}의 디스플레이 코드 판독 성공률(read_code_prob={sc.drone.read_code_prob:.0%})로는 "
                    f"메시지 {', '.join(low)} 전달률 기준 {params.min_delivery:.0%}을 못 맞춥니다. 판독 재시도 횟수나 코드 크기를 재검토하세요."
                )
            elif node is not None:
                issues.append(
                    f"노드 {nid}는 재밍 밖인데도 BLE 교신 실패가 많습니다(ble_range_m={sc.drone.node_range_m(node):.1f}). "
                    f"메시지 {', '.join(low)} 전달률 미달. 교신 고도/범위 파라미터를 점검하세요."
                )
            else:
                issues.append(
                    f"메시지 {', '.join(low)}의 전달률이 기준 {params.min_delivery:.0%} 미만입니다. "
                    "노드 교신은 성공하지만 운반 경로(태그 인식 실패 등)에서 손실됩니다."
                )
            break
        if dom == "collision" and params.margin_m < MARGIN_MAX:
            params.margin_m = round(min(MARGIN_MAX, params.margin_m + 0.15), 3)
            params.w_clear += 1.0
            trace.append(TraceStep(it, "revise", f"충돌이 지배적 -> 마진 {params.margin_m:.2f} m, 장애물 페널티 {params.w_clear:.1f}"))
        elif dom in ("tag_miss", "goal_miss") and params.z < Z_MAX:
            params.z = min(Z_MAX, params.z + 0.5)
            trace.append(TraceStep(it, "revise", f"{dom}가 지배적 -> 오차 허용 배수 z={params.z:.1f}로 상향(태그 간격 축소)"))
        elif dom == "link_abort" and params.jam_margin_m < 2.0:
            params.jam_margin_m = min(2.0, params.jam_margin_m + 0.5)
            trace.append(TraceStep(it, "revise", f"재밍 구역 진입이 지배적 -> 이격 {params.jam_margin_m:.1f} m 로 확대"))
        else:
            issues.append(f"자동 수정으로 해결되지 않는 실패 유형: {dom}")
            break

    if not success and best is not None:
        _, plan, mc, sc_b, params_b = best
        sc, params = sc_b, params_b
        grid.set_params(params)
    elif success:
        plan, mc = plan, mc  # type: ignore[assignment]
    else:
        plan, mc = (last_plan if last_plan and last_plan.ok else last_plan), None

    suggested = [t for t in sc.tags if t.suggested]
    if not success and not issues and not analysis.blocking:
        issues.append("반복 한도 내에 검증 기준을 통과하지 못했습니다.")
    return AgentResult(
        success=success,
        scenario=sc,
        params=params,
        analysis=analysis,
        plan=plan,
        mc=mc,
        grid=grid,
        trace=trace,
        suggested_tags=suggested,
        time_budget_s=budget,
        issues=issues,
        suggested_roles=[t for t in sc.tags if t.role_suggested],
    )

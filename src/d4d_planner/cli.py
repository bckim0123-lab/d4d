"""명령행 인터페이스: python -m d4d_planner {plan,analyze} ..."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import AgentConfig, run_agent
from .analysis import analyze
from .export import write_all
from .grid import Grid
from .model import PlanParams
from .scenario import ScenarioError, load_scenario


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("scenario", help="시나리오 JSON 경로")
    p.add_argument("--policy", choices=["abort", "continue_mission"], help="시나리오의 link_policy를 덮어쓴다")


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="d4d_planner", description="EMP/재밍 환경 드론 사전 전장분석·코스계획 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="분석 -> 계획 -> 검증 -> 산출물 생성")
    _common(pl)
    pl.add_argument("--out", default="out", help="출력 폴더 (기본: out)")
    pl.add_argument("--runs", type=int, default=200, help="몬테카를로 횟수")
    pl.add_argument("--seed", type=int, default=7)
    pl.add_argument("--no-suggest-tags", action="store_true", help="부족한 구간에 태그를 제안하지 않는다")

    an = sub.add_parser("analyze", help="전장 분석만 수행")
    _common(an)

    args = ap.parse_args(argv)
    try:
        sc = load_scenario(args.scenario)
    except ScenarioError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    if args.policy:
        sc.link_policy = args.policy

    if args.cmd == "analyze":
        params = PlanParams()
        a = analyze(sc, Grid(sc, params), params)
        for k, v in a.to_dict().items():
            print(f"{k}: {v}")
        return 1 if a.blocking else 0

    params = PlanParams(mc_runs=args.runs, seed=args.seed)
    res = run_agent(sc, params, AgentConfig(suggest_tags=not args.no_suggest_tags))
    files = write_all(res, Path(args.out))

    print(f"시나리오: {sc.name}  정책: {res.scenario.link_policy}")
    for s in res.trace:
        print(f"  [{s.it}] {s.tool}: {s.summary}")
    if res.plan and res.plan.ok:
        print(f"경로 {res.plan.total_length_m:.1f} m, 예상 {res.plan.total_time_s:.0f} s, 태그 {res.plan.tags_used}")
    if res.mc:
        print(f"시뮬 성공률 {res.mc.success_rate:.1%} 충돌 {res.mc.collision_rate:.1%} {res.mc.counts}")
        if res.scenario.messages:
            print("메시지 전달률: " + ", ".join(f"{k}={v:.1%}" for k, v in res.mc.delivery_by_msg.items()))
    for s in res.analysis.blocking + res.issues:
        print(f"! {s}")
    if res.suggested_tags:
        print("설치 필요 태그: " + ", ".join(f"T{t.id}({t.x:.1f},{t.y:.1f})" for t in res.suggested_tags))
    if res.suggested_roles:
        print("노드 역할 변경 제안: " + ", ".join(f"N{t.id} -> {t.role}" for t in res.suggested_roles))
    print("산출물:")
    for f in files:
        print(f"  {f}")
    print("결과:", "검증 통과" if res.success else "검증 미통과")
    return 0 if res.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

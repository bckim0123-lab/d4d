"""d4d_fc CLI: 비행제어기 조사(읽기 전용).

  python -m d4d_fc ports
  python -m d4d_fc inspect --port COM9 --out out\\fc
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import codes as C
from .inspect import gather, relevant_settings
from .msp import MspClient, MspError
from .readiness import evaluate, print_checks
from .tune import SYMPTOMS


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def list_ports() -> int:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        print("pyserial이 필요합니다: pip install pyserial", file=sys.stderr)
        return 2
    found = list(lp.comports())
    if not found:
        print("직렬 포트를 찾을 수 없습니다.")
        return 1
    for p in found:
        print(f"{p.device}\t{p.description}\tVID:PID={p.vid:04X}:{p.pid:04X}" if p.vid else f"{p.device}\t{p.description}")
    return 0


def _summary(data: dict) -> None:
    ident = data.get("identity", {})
    print(f"장치: {ident.get('variant')} {ident.get('firmware', {}).get('version')} "
          f"on {ident.get('board', {}).get('board_name') or ident.get('board', {}).get('board_id')}")
    b = ident.get("build", {})
    if b:
        print(f"빌드: {b.get('date')} {b.get('time')} {b.get('git')}")
    print(f"기체 이름: {ident.get('craft_name')}   UID: {ident.get('uid', {}).get('uid')}")

    st = data.get("status", {}).get("inav_status", {})
    if st:
        print(f"\n센서 감지: {', '.join(st.get('sensors', [])) or '없음'}")
        print(f"CPU 부하 {st.get('cpu_load_pct')}%  주기 {st.get('cycle_time_us')}us  I2C 오류 {st.get('i2c_errors')}")
        print(f"무장 상태: {'무장' if st.get('armed') else '해제'}")
        print(f"무장 차단 사유: {', '.join(st.get('arm_blockers', [])) or '없음'}")

    sc = data.get("status", {}).get("sensor_config", {})
    if sc:
        print(f"센서 설정: " + ", ".join(f"{k}={v}" for k, v in sc.items() if k != "raw"))

    live = data.get("live", {})
    if live.get("attitude"):
        a = live["attitude"]
        print(f"\n자세: roll {a.get('roll_deg')}deg pitch {a.get('pitch_deg')}deg yaw {a.get('yaw_deg')}deg")
    if live.get("inav_analog"):
        v = live["inav_analog"]
        print(f"전압 {v.get('vbat_V')}V (셀 {v.get('cell_count')})  전류 {v.get('amperage_A')}A")
    if live.get("rc"):
        print(f"RC 채널 {live['rc'].get('channel_count')}개: {live['rc'].get('channels_us')}")
    if live.get("motor_outputs_us"):
        print(f"모터 출력: {live['motor_outputs_us']}")

    ports = data.get("hardware", {}).get("serial_ports", [])
    if ports:
        print("\n직렬 포트 설정:")
        for p in ports:
            fn = ", ".join(p.get("functions", []))
            print(f"  {p.get('port_label'):12s} {fn:28s} MSP={p.get('msp_baud')} 주변장치={p.get('peripheral_baud')}")

    hw = data.get("hardware", {})
    if hw.get("motor_mixer"):
        print(f"\n모터 믹서 {hw.get('motor_count')}개:")
        for m in hw["motor_mixer"]:
            print(f"  M{m['motor']}: thr={m['throttle']:+.1f} roll={m['roll']:+.1f} "
                  f"pitch={m['pitch']:+.1f} yaw={m['yaw']:+.1f}")
    if hw.get("failsafe"):
        print(f"Failsafe: {hw['failsafe'].get('procedure')}  "
              f"지연 {hw['failsafe'].get('delay_ds', 0) / 10:.1f}초")

    md = data.get("modes", {})
    sw = md.get("configured_mode_switches", [])
    print(f"\n설정된 비행모드 스위치 {len(sw)}개" + (":" if sw else " (없음 - 조종기 설정 필요)"))
    for r in sw:
        print(f"  AUX{r.get('aux_channel')} {r.get('range_us')} -> {r.get('mode')}")
    if md.get("active_modes"):
        print(f"현재 활성 모드: {', '.join(md['active_modes'])}")
    if md.get("available_modes"):
        print(f"사용 가능 모드 {len(md['available_modes'])}개: {', '.join(md['available_modes'])}")

    s = data.get("settings", {})
    if s.get("count"):
        print(f"\n설정값: {s['count']}/{s['expected']}개 읽음 ({s['elapsed_s']}초)"
              + (f", 실패 {len(s['failed_indices'])}개" if s.get("failed_indices") else ""))
        rel = relevant_settings(s)
        print(f"  이 프로젝트 관련 설정 {len(rel)}개")

    if data.get("unsupported_commands"):
        print(f"\n미지원 명령: {data['unsupported_commands']}")


def _open(args):
    """--replay가 주어지면 스냅샷 재생, 아니면 실제 포트."""
    if getattr(args, "replay", None):
        from .replay import ReplayClient

        fc = ReplayClient.from_file(args.replay)
        print(f"(재생 모드: {args.replay})")
        return fc
    return MspClient(args.port, args.baud)


def _snapshot(args) -> int:
    import json
    from pathlib import Path

    from .snapshot import capture, settings_dump_lines

    try:
        fc = MspClient(args.port, args.baud)
    except Exception as e:
        print(f"[오류] 포트 열기 실패: {e}", file=sys.stderr)
        return 2
    try:
        snap = capture(fc, samples=args.samples, hz=args.hz, progress=lambda m: print(f"  {m}", flush=True))
    except MspError as e:
        print(f"[오류] 수집 실패: {e}", file=sys.stderr)
        return 2
    finally:
        fc.close()

    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    sp = d / "fc_snapshot.json"
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    dp = d / "fc_settings_dump.txt"
    dp.write_text("\n".join(settings_dump_lines(snap)) + "\n", encoding="utf-8")

    print(f"\n조회 응답 {len(snap['frames'])}개"
          + (f", 미지원 {len(snap['unsupported'])}개: {', '.join(snap['unsupported'])}" if snap["unsupported"] else ""))
    s = snap["settings"]
    print(f"설정 {s['count']}/{s['expected']}개 (원본 바이트 포함)")
    live = snap["live_series"]
    print(f"실시간 시계열 {live['samples']}회 x {len(live['data'])}종")
    print(f"\n저장: {sp}  ({sp.stat().st_size // 1024} KB)")
    print(f"      {dp}")
    print("\n케이블을 뽑은 뒤 사용법:")
    print(f"  python -m d4d_fc inspect --replay {sp}")
    print(f"  python -m d4d_fc monitor --replay {sp} --seconds 5")
    return 0


def _angle_always(args) -> int:
    from .msp import MspClient
    from .write import WriteBlocked, apply, plan_angle_always, read_entries, step_to_us

    if args.save and not args.apply:
        print("[오류] --save 는 --apply 와 함께 써야 합니다.", file=sys.stderr)
        return 2

    with MspClient(args.port) as fc:
        entries = read_entries(fc)
        print("현재 모드 범위:")
        for i, (pid, aux, s, t) in enumerate(entries):
            if s >= t:
                continue
            import d4d_fc.codes as C

            print(f"  슬롯 {i}: {C.BOX_NAMES.get(pid, f'id{pid}')} "
                  f"AUX{aux + 1}(RC {aux + 5}번) {step_to_us(s)}~{step_to_us(t)}us")

        try:
            plan = plan_angle_always(entries, args.aux, args.keep_existing)
        except WriteBlocked as e:
            print(f"[오류] {e}", file=sys.stderr)
            return 1

        if plan.noop:
            print("")
            for n in plan.notes:
                print(f"  {n}")
            return 0

        print("\n변경 계획:")
        for ln in plan.describe():
            print("  " + ln)
        for n in plan.notes:
            print(f"  - {n}")

        if not args.apply:
            print("\n미리보기입니다. 실제로 바꾸려면 --apply 를 붙이세요.")
            print("  RAM만 적용:   --apply          (전원 뽑으면 원복)")
            print("  영구 저장:     --apply --save")
            return 0

        try:
            res = apply(fc, plan, save=args.save)
        except WriteBlocked as e:
            print(f"\n[중단] {e}", file=sys.stderr)
            return 1

        print(f"\n백업: {res['backup']}")
        print(f"되읽기 검증: {'일치' if res['verified'] else '불일치'} "
              f"(기대 {res['expected']}, 실제 {res['actual']})")
        if res.get("active_modes") is not None:
            on = ", ".join(res["active_modes"]) or "(없음)"
            print(f"지금 활성 모드: {on}")
            if "ANGLE" not in res["active_modes"]:
                print("  주의: ANGLE 이 활성 목록에 없습니다. 값은 들어갔지만 "
                      "실제로 켜지지 않았습니다.")
        if res["saved"]:
            print("EEPROM 저장 완료. 전원을 다시 넣어도 유지됩니다.")
        else:
            print("EEPROM에 저장하지 않았습니다. 전원을 뽑으면 원래대로 돌아갑니다.")
    return 0


def _log(args) -> int:
    from pathlib import Path

    from . import bbl
    from .logan import analyze, render

    p = Path(args.file)
    if not p.exists():
        print(f"[오류] 파일이 없습니다: {p}", file=sys.stderr)
        return 2

    try:
        sess = bbl.sessions(p)
    except bbl.BblError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    if not sess:
        print("[오류] 해석할 수 있는 세션이 없습니다.", file=sys.stderr)
        return 2

    print(f"{p.name}: 세션 {len(sess)}개")
    for s in sess:
        print(f"  [{s['index']}] {s['bytes'] / 1024:.0f}KB  필드 {s['fields']}개  "
              f"{s['firmware']}  {s['craft']}")
    if args.list:
        return 0

    idx = args.session if args.session is not None else max(sess, key=lambda s: s["bytes"])["index"]
    print(f"\n세션 {idx} 해석 중...")
    secs = None if args.seconds == 0 else args.seconds
    try:
        d = bbl.decode_file(p, max_seconds=secs, session=idx)
    except bbl.BblError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    if not d.rows:
        print("[오류] 메인 프레임을 하나도 해석하지 못했습니다. 디코더가 이 로그와 "
              "맞지 않습니다.", file=sys.stderr)
        return 1

    a = analyze(d, nfft=args.nfft)
    lines = render(d, a)
    for ln in lines:
        print(ln)

    if args.out:
        dd = Path(args.out)
        dd.mkdir(parents=True, exist_ok=True)
        f = dd / f"log_{p.stem}_s{idx}.md"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n저장: {f}")
    return 0


def _tune(args) -> int:
    import json
    from pathlib import Path

    from .tune import LOG_READING, MAIDEN_STEPS, blackbox_status, blockers, propose, read_tuning, render_report

    p = Path(args.snapshot)
    if not p.exists():
        print(f"[오류] 스냅샷이 없습니다: {p}\n  먼저: python -m d4d_fc snapshot --port COM9", file=sys.stderr)
        return 2
    snap = json.loads(p.read_text(encoding="utf-8"))
    t = read_tuning(snap)
    prop = propose(t, args.symptom) if args.symptom else None

    print(f"EZ Tune: {'ON' if t.ez_enabled else 'OFF'}")
    if t.ez_enabled:
        print("  -> 원시 PID는 슬라이더에서 재계산됩니다. 직접 수정해도 유지되지 않습니다.")
        print("  슬라이더: " + ", ".join(f"{n.replace('ez_', '')}={v}" for n, v in t.group(
            ("ez_filter_hz", "ez_response", "ez_damping", "ez_stability", "ez_aggressiveness"))))
    print("현재 PID: " + ", ".join(f"{n.replace('mc_', '')}={v}" for n, v in t.group(
        ("mc_p_roll", "mc_i_roll", "mc_d_roll", "mc_p_pitch", "mc_i_pitch", "mc_d_pitch"))))
    print(f"필터: gyro_main_lpf_hz={t.val('gyro_main_lpf_hz')}, dterm_lpf_hz={t.val('dterm_lpf_hz')}, "
          f"gyro_filter_mode={t.val('gyro_filter_mode')}")

    ok, bb_lines = blackbox_status(t)
    print(f"\n블랙박스: {'사용 가능' if ok else '사용 불가'}")
    for x in bb_lines:
        print(f"  {x}")

    b = blockers(t)
    if b:
        print("\n튜닝 전에 필요한 것:")
        for x in b:
            print(f"  ! {x}")

    if prop:
        print(f"\n증상 '{prop.symptom}': {prop.description}")
        for n in prop.notes:
            print(f"  - {n}")
        if prop.mechanical_first:
            print("  먼저 확인할 기계적 원인: " + ", ".join(prop.mechanical_first))
        if prop.changes:
            print("\n  제안 (한 번에 하나씩 적용하고 호버 시험):")
            for c in prop.changes:
                print(f"    set {c.setting} = {c.proposed}   (현재 {c.current}) - {c.reason}")
        if prop.procedure:
            print("\n  첫 호버 절차:")
            for i, s in enumerate(MAIDEN_STEPS, 1):
                print(f"    {i}. {s}")
            print("\n  로그를 보고 증상 고르기:")
            for x in LOG_READING:
                print(f"    - {x}")
    else:
        print("\n--symptom 을 주면 변경안을 만듭니다. 가능: " + ", ".join(sorted(SYMPTOMS)))

    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("tune_report.md" if not args.symptom else f"tune_{args.symptom}.md")
        f.write_text("\n".join(render_report(t, prop)) + "\n", encoding="utf-8")
        print(f"\n저장: {f}")
    return 0


def _monitor(args) -> int:
    import time

    from . import decode as D

    if not args.port and not args.replay:
        print("[오류] --port 또는 --replay 중 하나가 필요합니다.", file=sys.stderr)
        return 2
    try:
        fc = _open(args)
    except Exception as e:
        print(f"[오류] 열기 실패: {e}", file=sys.stderr)
        return 2
    ids = fc.try_request(C.MSP_BOXIDS)
    boxids = list(ids) if ids else []
    print("시간  roll/pitch/yaw      고도    거리     광류    RC(1-8)                         활성모드")
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            att = fc.try_request(C.MSP_ATTITUDE)
            alt = fc.try_request(C.MSP_ALTITUDE)
            rf = fc.try_request(C.MSP_SONAR_ALTITUDE)
            of = fc.try_request(C.MSP2_INAV_OPTICAL_FLOW)
            rcp = fc.try_request(C.MSP_RC)
            ab = fc.try_request(C.MSP_ACTIVEBOXES)
            a = D.attitude(att) if att else {}
            al = D.altitude(alt) if alt else {}
            r = D.sonar_altitude(rf) if rf else {}
            o = D.opflow(of) if of else {}
            ch = D.rc(rcp).get("channels_us", []) if rcp else []
            boxes = D.active_boxes(ab, boxids) if (ab and boxids) else []
            print(
                f"{time.monotonic() - t0:5.1f} "
                f"{a.get('roll_deg', 0):6.1f}/{a.get('pitch_deg', 0):6.1f}/{a.get('yaw_deg', 0):5.0f} "
                f"{al.get('est_alt_m', 0):7.2f}m {str(r.get('rangefinder_cm', '-')):>6s} "
                f"q={str(o.get('quality', '-')):>4s} "
                f"{str(ch[:8]):32s} {','.join(boxes)}",
                flush=True,
            )
            time.sleep(max(0.0, 1.0 / args.hz))
    except KeyboardInterrupt:
        pass
    finally:
        fc.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(prog="d4d_fc", description="비행제어기 조사 도구 (읽기 전용)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="직렬 포트 목록")

    ins = sub.add_parser("inspect", help="FC 정보를 최대한 수집 (또는 스냅샷 재생)")
    ins.add_argument("--port", help="직렬 포트 (예: COM9)")
    ins.add_argument("--replay", help="케이블 없이 스냅샷 파일로 실행")
    ins.add_argument("--baud", type=int, default=115200)
    ins.add_argument("--out", default=None, help="JSON 저장 폴더")
    ins.add_argument("--no-settings", action="store_true", help="전체 설정값 덤프를 건너뛴다")

    mon = sub.add_parser("monitor", help="자세/RC/센서를 실시간으로 표시(읽기 전용)")
    mon.add_argument("--port")
    mon.add_argument("--replay", help="케이블 없이 스냅샷 시계열을 재생")
    mon.add_argument("--baud", type=int, default=115200)
    mon.add_argument("--hz", type=float, default=5.0)
    mon.add_argument("--seconds", type=float, default=10.0)

    tn = sub.add_parser("tune", help="PID/필터 현황 분석과 변경안 생성 (적용하지 않음)")
    tn.add_argument("--snapshot", default="snapshots/fc_snapshot.json", help="스냅샷 파일")
    tn.add_argument("--symptom", choices=sorted(SYMPTOMS), help="흔들림 증상")
    tn.add_argument("--out", default=None, help="보고서 저장 폴더")

    aa = sub.add_parser("angle-always", help="ANGLE 모드를 상시 활성으로 설정 (유일한 쓰기 기능)")
    aa.add_argument("--port", required=True, help="예: COM9")
    aa.add_argument("--aux", type=int, default=None, help="쓸 AUX 채널 번호(0부터). 기본: 빈 채널 자동")
    aa.add_argument("--keep-existing", action="store_true", help="기존 ANGLE 규칙을 남긴다")
    aa.add_argument("--apply", action="store_true", help="RAM에 적용 (전원 뽑으면 되돌아감)")
    aa.add_argument("--save", action="store_true", help="EEPROM에 영구 저장 (--apply 필요)")

    lg = sub.add_parser("log", help="블랙박스 로그(.BBL) 분석")
    lg.add_argument("file", help=".BBL / .BFL 파일 경로")
    lg.add_argument("--session", type=int, default=None, help="세션 번호 (기본: 가장 긴 것)")
    lg.add_argument("--seconds", type=float, default=30.0, help="분석할 길이. 0이면 전체")
    lg.add_argument("--nfft", type=int, default=1024, help="FFT 점수 (2의 거듭제곱)")
    lg.add_argument("--list", action="store_true", help="세션 목록만 보기")
    lg.add_argument("--out", default=None, help="보고서 저장 폴더")

    snp = sub.add_parser("snapshot", help="오프라인 개발용 전체 스냅샷 수집 (읽기 전용)")
    snp.add_argument("--port", required=True)
    snp.add_argument("--baud", type=int, default=115200)
    snp.add_argument("--out", default="snapshots", help="저장 폴더 (기본: snapshots)")
    snp.add_argument("--samples", type=int, default=40, help="실시간 샘플 수")
    snp.add_argument("--hz", type=float, default=10.0)

    args = ap.parse_args(argv)
    if args.cmd == "ports":
        return list_ports()
    if args.cmd == "snapshot":
        return _snapshot(args)
    if args.cmd == "tune":
        return _tune(args)
    if args.cmd == "log":
        return _log(args)
    if args.cmd == "angle-always":
        return _angle_always(args)
    if args.cmd == "monitor":
        return _monitor(args)
    if not args.port and not args.replay:
        print("[오류] --port 또는 --replay 중 하나가 필요합니다.", file=sys.stderr)
        return 2

    def prog(i, n, el):
        print(f"  설정 읽기 {i}/{n} ({el:.0f}s)", flush=True)

    try:
        with _open(args) as fc:
            data = gather(fc, with_settings=not args.no_settings, settings_progress=prog)
    except MspError as e:
        print(f"[오류] 통신 실패: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[오류] 열기 실패: {e}", file=sys.stderr)
        return 2

    _summary(data)

    print("\n--- 이 프로젝트(EMP/재밍 자율비행) 준비 상태 ---")
    checks = evaluate(data)
    print_checks(checks)

    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "fc_inspect.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "fc_readiness.md").write_text(
            "# 프로젝트 준비 상태 점검\n\n"
            + "\n".join(
                f"- **[{c.level}] {c.name}**: {c.detail}" + (f"\n  - 조치: {c.action}" if c.action else "")
                for c in checks
            )
            + "\n",
            encoding="utf-8",
        )
        s = data.get("settings", {})
        if s.get("items"):
            lines = ["# INAV 설정 전체 덤프", "", "| index | name | type | section | value | min | max |", "|---|---|---|---|---|---|---|"]
            for it in s["items"]:
                val = it.get("value_name") or it.get("value", it.get("value_raw"))
                lines.append(f"| {it.get('index')} | `{it.get('name')}` | {it.get('type')} | {it.get('section')} | {val} | {it.get('min')} | {it.get('max')} |")
            (d / "fc_settings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n저장: {d / 'fc_inspect.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

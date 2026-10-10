"""PID/필터 튜닝 상태 분석과 변경안 생성.

이 모듈은 **값을 적용하지 않는다.** 현재 상태를 읽고, 증상에 따른 변경안을
설정 범위 안에서 계산해 기록만 한다. 실제 적용과 시험비행은 사람이 판단할 일이다.

중요: INAV의 EZ Tune이 켜져 있으면 mc_p_*/mc_i_*/mc_d_*/mc_cd_* 와 자이로·D텀
필터를 EZ Tune 슬라이더에서 매 루프 다시 계산한다. 그래서 EZ Tune이 ON인 동안
원시 PID를 직접 바꿔도 되돌려진다. 튜닝은 슬라이더로 해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 읽기

EZ_SLIDERS = ("ez_enabled", "ez_filter_hz", "ez_axis_ratio", "ez_response", "ez_damping",
              "ez_stability", "ez_aggressiveness", "ez_rate", "ez_expo", "ez_snappiness")

RAW_PIDS = tuple(f"mc_{t}_{a}" for a in ("roll", "pitch", "yaw") for t in ("p", "i", "d", "cd"))

FILTERS = ("gyro_main_lpf_hz", "gyro_anti_aliasing_lpf_hz", "gyro_filter_mode",
           "gyro_dyn_lpf_min_hz", "gyro_dyn_lpf_max_hz", "dterm_lpf_hz", "dterm_lpf_type",
           "dynamic_gyro_notch_enabled", "dynamic_gyro_notch_mode", "dynamic_gyro_notch_q",
           "dynamic_gyro_notch_min_hz", "setpoint_kalman_enabled", "setpoint_kalman_q")

HELPERS = ("mc_iterm_relax", "mc_iterm_relax_cutoff", "iterm_windup", "d_boost_min", "d_boost_max",
           "antigravity_gain", "tpa_rate", "tpa_breakpoint", "rate_accel_limit_roll_pitch",
           "smith_predictor_strength", "smith_predictor_delay", "looptime",
           "motor_pwm_protocol", "motor_pwm_rate")

NAV_PIDS = ("nav_mc_pos_xy_p", "nav_mc_vel_xy_p", "nav_mc_vel_xy_i", "nav_mc_vel_xy_d",
            "nav_mc_vel_xy_ff", "nav_mc_pos_z_p", "nav_mc_vel_z_p", "nav_mc_vel_z_i",
            "nav_mc_vel_z_d", "nav_mc_heading_p")

LEVEL_PIDS = ("mc_p_level", "mc_i_level", "mc_d_level")


def _index(items: list[dict]) -> dict[str, dict]:
    return {x["name"]: x for x in items if "name" in x}


@dataclass
class Tuning:
    settings: dict[str, dict]
    ez_enabled: bool
    blackbox_device: int | None = None
    sdcard: dict | None = None
    was_ever_armed: bool | None = None

    def val(self, name: str):
        s = self.settings.get(name)
        if not s:
            return None
        return s.get("value_name", s.get("value"))

    def group(self, names) -> list[tuple[str, Any]]:
        return [(n, self.val(n)) for n in names if n in self.settings]


def read_tuning(snapshot_or_inspect: dict) -> Tuning:
    items = snapshot_or_inspect.get("settings", {}).get("items", [])
    idx = _index(items)
    ez = idx.get("ez_enabled", {}).get("value_name") == "ON"

    sd = None
    armed = None
    frames = snapshot_or_inspect.get("frames", {})
    # blackbox_device는 반드시 설정에서 읽는다. INAV 9의 MSP_BLACKBOX_CONFIG(80)는
    # 더 이상 지원되지 않아 항상 00000000 을 돌려준다. 그 0을 device로 읽으면
    # 블랙박스가 꺼진 것으로 오진한다.
    bb = idx.get("blackbox_device", {}).get("value")
    if "MSP_SDCARD_SUMMARY" in frames:
        raw = bytes.fromhex(frames["MSP_SDCARD_SUMMARY"]["hex"])
        if len(raw) >= 11:
            import struct

            free, total = struct.unpack_from("<II", raw, 3)
            sd = {"flags": raw[0], "state": raw[1], "last_error": raw[2],
                  "free_gb": round(free / 1e6, 2), "total_gb": round(total / 1e6, 2)}
    st = snapshot_or_inspect.get("status", {}).get("inav_status")
    if st:
        armed = "WAS_EVER_ARMED" in st.get("arming", [])
    elif "MSP2_INAV_STATUS" in frames:
        import struct

        raw = bytes.fromhex(frames["MSP2_INAV_STATUS"]["hex"])
        if len(raw) >= 13:
            armed = bool(struct.unpack_from("<I", raw, 9)[0] & (1 << 3))
    return Tuning(idx, ez, bb, sd, armed)


# ---------------------------------------------------------------- 증상별 변경안


@dataclass
class Change:
    setting: str
    current: Any
    proposed: Any
    reason: str
    clamped: bool = False


@dataclass
class Proposal:
    symptom: str
    description: str
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    mechanical_first: list[str] = field(default_factory=list)
    procedure: bool = False


# 증상 -> (설명, EZ Tune 슬라이더 변화량, 원시 PID 변화 비율, 메모)
# 변화량은 '한 단계'다. 한 번에 하나씩 바꾸고 호버 시험으로 확인해야 한다.
SYMPTOMS: dict[str, dict] = {
    "highfreq": {
        "desc": "고주파 떨림/윙윙거림. 모터나 ESC가 뜨거워지고 소리가 거칠다.",
        "ez": [("ez_filter_hz", -20, "필터 주파수를 낮춰 자이로 노이즈가 D텀으로 덜 들어가게 한다"),
               ("ez_damping", -10, "D텀을 줄인다. 고주파 진동은 D가 노이즈를 증폭할 때 생긴다")],
        "raw": [("mc_d_roll", -0.15, "D텀 감소"), ("mc_d_pitch", -0.15, "D텀 감소"),
                ("gyro_main_lpf_hz", -0.2, "자이로 저역통과 강화"),
                ("dterm_lpf_hz", -0.2, "D텀 저역통과 강화")],
        "mech": ["프로펠러 균형과 손상 여부", "모터 베어링 유격", "FC 소프트마운트와 배선 고정",
                 "프레임 암 나사 조임"],
    },
    "wobble": {
        "desc": "저주파 흔들림(눈에 보이는 1~5Hz 요동). 기체가 천천히 좌우로 흔들린다.",
        "ez": [("ez_response", -10, "setpoint 추종을 완만하게 해 과도한 P 반응을 줄인다"),
               ("ez_stability", -10, "전체 강성을 낮춘다")],
        "raw": [("mc_p_roll", -0.15, "P 감소"), ("mc_p_pitch", -0.15, "P 감소"),
                ("mc_i_roll", -0.15, "I 감소"), ("mc_i_pitch", -0.15, "I 감소")],
        "mech": ["기체 무게중심", "배터리 고정 상태(흔들리면 저주파 요동이 생긴다)"],
    },
    "bounce": {
        "desc": "스틱을 멈춘 뒤 되튐(오버슈트). 급기동 끝에서 한두 번 출렁인다.",
        "ez": [("ez_damping", 10, "D텀을 키워 정지 시 감쇠를 늘린다"),
               ("ez_response", -5, "응답을 약간 완만하게 한다")],
        "raw": [("mc_d_roll", 0.15, "D텀 증가"), ("mc_d_pitch", 0.15, "D텀 증가"),
                ("mc_iterm_relax_cutoff", 0.2, "급기동 중 I 누적 완화")],
        "mech": [],
    },
    "propwash": {
        "desc": "빠른 하강이나 급기동 중에만 흔들린다(프롭워시).",
        "ez": [("ez_damping", 10, "D텀 증가"), ("ez_stability", 5, "강성 증가"),
               ("ez_aggressiveness", 5, "I 응답 증가")],
        "raw": [("mc_d_roll", 0.1, "D텀 증가"), ("mc_d_pitch", 0.1, "D텀 증가")],
        "mech": ["프로펠러 상태", "기체 추력 여유(호버 스로틀이 너무 높지 않은지)"],
    },
    "firstflight": {
        "desc": "아직 비행하지 않았다. 첫 호버를 최대한 얌전하게 만들고 로그를 남긴다.",
        # 안정화 슬라이더는 건드리지 않는다. 로그가 없으면 어느 방향이 맞는지 알 수 없고,
        # 현재 값(damping 115 / aggressiveness 80)은 이미 보수적이다. 조종 반응만 낮춘다.
        "ez": [("ez_rate", ("=", 100), "스틱 반응을 기본값으로 낮춰 과조작을 줄인다. "
                                       "첫 비행에서 흔들림의 상당수는 조종사가 만든다")],
        "raw": [("ez_rate", ("=", 100), "스틱 반응을 기본값으로 낮춘다")],
        "mech": ["프로펠러 장착 방향과 균형", "모터 회전 방향", "모터·암 나사 조임",
                 "FC 소프트마운트", "배터리 고정", "무게중심"],
        "note": "비행 전에는 PID를 추측으로 바꾸지 않는다. 현재 EZ Tune 값은 이미 보수적인 편이다. "
                "호버 로그를 먼저 받고 그 로그로 증상을 특정하는 것이 정확하다.",
        "procedure": True,
    },
    "poshold": {
        "desc": "자세는 안정한데 위치유지/고도유지 중 천천히 왕복한다.",
        "ez": [],
        "raw": [("nav_mc_vel_xy_p", -0.2, "속도 P 감소"), ("nav_mc_vel_xy_d", 0.2, "속도 D 증가"),
                ("nav_mc_pos_xy_p", -0.15, "위치 P 감소")],
        "mech": ["광류/거리 센서 장착과 바닥 무늬", "진동이 위치추정에 섞이는지"],
        "note": "이 증상은 자세 PID가 아니라 항법 PID 문제다. EZ Tune은 항법 PID를 건드리지 않는다.",
    },
}


def _clamp(s: dict, v: float) -> tuple[Any, bool]:
    lo, hi = s.get("min"), s.get("max")
    out = v
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    is_float = s.get("type") == "float"
    out = round(float(out), 3) if is_float else int(round(out))
    return out, out != (round(float(v), 3) if is_float else int(round(v)))


def propose(t: Tuning, symptom: str) -> Proposal:
    if symptom not in SYMPTOMS:
        raise ValueError(f"알 수 없는 증상: {symptom}. 가능: {', '.join(SYMPTOMS)}")
    spec = SYMPTOMS[symptom]
    p = Proposal(symptom, spec["desc"])
    p.procedure = bool(spec.get("procedure"))
    p.mechanical_first = list(spec.get("mech", []))
    if spec.get("note"):
        p.notes.append(spec["note"])

    use_ez = t.ez_enabled and spec["ez"]
    if t.ez_enabled and not spec["ez"]:
        p.notes.append("EZ Tune이 켜져 있지만 이 증상은 EZ Tune 범위 밖이므로 해당 PID를 직접 조정한다.")
    elif t.ez_enabled:
        p.notes.append("EZ Tune이 켜져 있어 원시 PID 대신 슬라이더를 조정한다. "
                       "EZ Tune이 ON인 동안 mc_p_*/mc_i_*/mc_d_* 직접 수정은 덮어쓰기된다.")
    else:
        p.notes.append("EZ Tune이 꺼져 있어 원시 PID를 직접 조정한다.")

    entries = spec["ez"] if use_ez else spec["raw"]
    for name, delta, reason in entries:
        s = t.settings.get(name)
        if not s or "value" not in s:
            p.notes.append(f"{name}: 이 펌웨어에 없거나 값을 읽지 못해 건너뜀")
            continue
        cur = s["value"]
        if isinstance(delta, tuple) and delta[0] == "=":
            new = delta[1]
        elif use_ez:
            new = cur + delta  # EZ 슬라이더는 절대값 가감
        else:
            new = cur * (1.0 + delta)  # 원시 PID는 비율
        val, clamped = _clamp(s, new)
        if val == cur:
            p.notes.append(f"{name}: 이미 한계값({cur})이라 변경 없음")
            continue
        p.changes.append(Change(name, cur, val, reason, clamped))
    return p


# ---------------------------------------------------------------- 첫 비행 절차

MAIDEN_STEPS = [
    "프로펠러를 **빼고** 무장해 모터 회전 방향과 순서를 확인한다.",
    "프로펠러를 달고, 바람 없는 넓은 곳에서 기체를 바닥에 놓는다.",
    "ANGLE 모드로 무장한 뒤 바닥에서 뜨지 않을 만큼만 스로틀을 올려 기체가 "
    "한쪽으로 기울거나 떨리는지 본다. 이상하면 즉시 내린다.",
    "1m 정도 높이에서 20~30초 호버한다. 스틱은 가능한 가만히 둔다.",
    "착륙하고 **무장을 해제한 뒤** 배터리를 뺀다. 이때까지 로그가 기록된다.",
    "모터와 ESC를 손으로 만져 온도를 확인한다. 뜨겁다면 D텀이나 노이즈 문제다.",
    "SD카드를 빼서 `LOGS/` 폴더의 `.BBL` 파일을 INAV Blackbox Explorer로 연다.",
    "자이로 스펙트럼에서 봉우리 주파수를 확인하고, 그 값으로 증상을 특정한다.",
]

LOG_READING = [
    "스펙트럼 봉우리가 **150Hz 이상**이면 프레임·프롭 노이즈다 → `--symptom highfreq`",
    "봉우리가 **1~5Hz**이고 자세가 천천히 요동하면 → `--symptom wobble`",
    "스틱을 멈춘 지점에서 자세가 넘어갔다 돌아오면 → `--symptom bounce`",
    "하강 구간에서만 흔들리면 → `--symptom propwash`",
    "자세는 깨끗한데 위치가 왕복하면 → `--symptom poshold`",
]


# ---------------------------------------------------------------- 보고


BLACKBOX_DEVICES = {0: "NONE", 1: "DATAFLASH", 2: "SDCARD", 3: "SERIAL"}


def blackbox_status(t: Tuning) -> tuple[bool, list[str]]:
    """(로깅 가능한가, 설명 줄들)."""
    dev = t.blackbox_device
    name = BLACKBOX_DEVICES.get(dev, f"알 수 없음({dev})")
    lines = [f"blackbox_device = {name}"]
    if dev == 2:
        if t.sdcard and t.sdcard.get("state") == 4:
            lines.append(f"SD카드 정상 인식, {t.sdcard['free_gb']}GB / {t.sdcard['total_gb']}GB 사용 가능")
            num = t.val("blackbox_rate_num")
            den = t.val("blackbox_rate_denom")
            if num and den:
                hz = None
                lt = t.val("looptime")
                if lt:
                    hz = int(1e6 / lt * num / den)
                lines.append(f"기록 비율 {num}/{den}" + (f" (약 {hz}Hz)" if hz else ""))
            return True, lines
        st = (t.sdcard or {}).get("state")
        lines.append(f"SD카드가 준비되지 않았습니다(state={st}). 카드를 넣고 포맷을 확인하세요.")
        return False, lines
    if dev == 0:
        lines.append("로깅이 꺼져 있습니다. `set blackbox_device = SDCARD` 로 켜야 합니다.")
        return False, lines
    return True, lines


def blockers(t: Tuning) -> list[str]:
    """제대로 튜닝하기 전에 갖춰야 할 것."""
    out = []
    ok, _ = blackbox_status(t)
    if not ok:
        out.append("블랙박스 로깅이 준비되지 않았습니다. 로그 없이 하는 PID 튜닝은 추측입니다.")
    if t.was_ever_armed is False:
        out.append("전원이 들어온 뒤 한 번도 무장되지 않았습니다. 비행 데이터가 없으므로 "
                   "증상 기반 변경안은 추정입니다. 먼저 호버 로그를 받으세요.")
    if t.val("failsafe_procedure") == "DROP":
        out.append("failsafe_procedure = DROP 입니다. 링크가 끊기면 모터가 즉시 멈춰 추락합니다. "
                   "첫 비행 전에 LAND 로 바꾸는 것을 권합니다.")
    return out


def render_report(t: Tuning, p: Proposal | None = None) -> list[str]:
    L = ["# PID/필터 튜닝 현황", ""]
    L.append(f"- EZ Tune: **{'ON' if t.ez_enabled else 'OFF'}**")
    if t.ez_enabled:
        L.append("  - EZ Tune이 ON이면 원시 PID와 자이로·D텀 필터를 슬라이더에서 다시 계산합니다. "
                 "**원시 PID 직접 수정은 유지되지 않습니다.**")
    L.append("")

    def table(title, pairs):
        if not pairs:
            return
        L.append(f"## {title}")
        L.append("")
        L.append("| 설정 | 값 | 범위 |")
        L.append("|---|---|---|")
        for n, v in pairs:
            s = t.settings.get(n, {})
            L.append(f"| `{n}` | {v} | {s.get('min')}..{s.get('max')} |")
        L.append("")

    table("EZ Tune 슬라이더", t.group(EZ_SLIDERS))
    table("현재 적용된 원시 PID", t.group(RAW_PIDS))
    table("자세 안정화(레벨) PID", t.group(LEVEL_PIDS))
    table("필터", t.group(FILTERS))
    table("보조 기능", t.group(HELPERS))
    table("항법 PID", t.group(NAV_PIDS))

    ok, bb_lines = blackbox_status(t)
    L.append(f"## 블랙박스 로깅: {'사용 가능' if ok else '사용 불가'}")
    L.append("")
    for x in bb_lines:
        L.append(f"- {x}")
    L.append("")

    b = blockers(t)
    if b:
        L.append("## 튜닝 전에 필요한 것")
        L.append("")
        for x in b:
            L.append(f"- {x}")
        L.append("")

    if p:
        L.append(f"## 변경안: {p.symptom}")
        L.append("")
        L.append(f"증상: {p.description}")
        L.append("")
        for n in p.notes:
            L.append(f"- {n}")
        L.append("")
        if p.mechanical_first:
            L.append("**PID보다 먼저 확인할 기계적 원인** (여기 해당하면 튜닝으로 해결되지 않습니다)")
            L.append("")
            for m in p.mechanical_first:
                L.append(f"- {m}")
            L.append("")
        if p.changes:
            L.append("| 설정 | 현재 | 제안 | 이유 |")
            L.append("|---|---|---|---|")
            for c in p.changes:
                mark = " (범위 제한)" if c.clamped else ""
                L.append(f"| `{c.setting}` | {c.current} | **{c.proposed}**{mark} | {c.reason} |")
            L.append("")
            L.append("### 적용 방법 (INAV CLI)")
            L.append("")
            L.append("```")
            for c in p.changes:
                L.append(f"set {c.setting} = {c.proposed}")
            L.append("save")
            L.append("```")
            L.append("")
            L.append("**한 번에 하나씩** 바꾸고 호버 시험으로 확인하세요. 여러 개를 동시에 바꾸면 "
                     "어느 것이 효과를 냈는지 알 수 없습니다. 변경 전 "
                     "`snapshots/fc_settings_dump.txt`가 복구 기록입니다.")
        else:
            L.append("제안할 변경이 없습니다.")
        L.append("")

        if p.procedure:
            L.append("## 첫 호버 절차")
            L.append("")
            for i, s in enumerate(MAIDEN_STEPS, 1):
                L.append(f"{i}. {s}")
            L.append("")
            L.append("## 로그를 보고 증상 고르기")
            L.append("")
            for x in LOG_READING:
                L.append(f"- {x}")
            L.append("")
    return L

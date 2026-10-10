"""FC 설정 쓰기 — 모드 범위 한 가지만 다룬다.

이 프로젝트의 나머지 도구는 모두 읽기 전용이다. 이 모듈만 예외이고,
허용 명령을 두 개로 못 박았다(`ALLOWED_WRITES`). 그 밖의 명령은 보내지 않는다.

안전 순서:
  1. 무장 상태면 거부한다.
  2. 현재 모드 범위를 백업 파일로 남긴다.
  3. **RAM에만** 쓴다. 이 상태는 전원을 뽑으면 원래대로 돌아간다.
  4. 되읽어 바이트가 일치하는지 확인한다. 다르면 거기서 멈춘다.
  5. 활성 박스까지 확인해 실제로 모드가 켜졌는지 본다.
  6. 사용자가 명시적으로 요청했을 때만 EEPROM에 저장한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import codes as C
from . import decode as D
from .msp import MspError

ALLOWED_WRITES = {C.MSP_SET_MODE_RANGE, C.MSP_EEPROM_WRITE}

# 모드 범위는 900us 기준 25us 단위 '스텝'으로 저장된다.
STEP_US = 25
BASE_US = 900
MAX_STEP = 48  # 900 + 25*48 = 2100
MAX_SLOTS = 20

BOX_ANGLE = 1  # 영구 ID


class WriteBlocked(Exception):
    """안전 조건을 만족하지 못해 쓰기를 거부했다."""


def _guard(cmd: int) -> None:
    if cmd not in ALLOWED_WRITES:
        raise WriteBlocked(
            f"명령 {cmd}(0x{cmd:X}) 은 이 모듈의 허용 목록에 없습니다. "
            f"허용: {sorted(ALLOWED_WRITES)}"
        )


def us_to_step(us: int) -> int:
    return max(0, min(MAX_STEP, round((us - BASE_US) / STEP_US)))


def step_to_us(step: int) -> int:
    return BASE_US + STEP_US * step


# ---------------------------------------------------------------- 읽기


def read_entries(fc) -> list[tuple[int, int, int, int]]:
    """MSP_MODE_RANGES 를 (영구ID, aux, 시작스텝, 끝스텝) 목록으로."""
    p = fc.request(C.MSP_MODE_RANGES, b"")
    return [tuple(p[i * 4: i * 4 + 4]) for i in range(len(p) // 4)]


def is_armed(fc) -> bool:
    raw = fc.try_request(C.MSP2_INAV_STATUS, b"")
    if raw is None:
        raise WriteBlocked("무장 상태를 확인할 수 없어 쓰기를 거부합니다.")
    return bool(D.inav_status(raw).get("armed"))


def active_modes(fc) -> list[str]:
    boxids = list(fc.request(C.MSP_BOXIDS, b""))
    mask = fc.request(C.MSP_ACTIVEBOXES, b"")
    return D.active_boxes(mask, boxids)


# ---------------------------------------------------------------- 계획


@dataclass
class Plan:
    slot: int
    before: tuple[int, int, int, int] | None
    after: tuple[int, int, int, int]
    notes: list[str] = field(default_factory=list)
    noop: bool = False

    @property
    def payload(self) -> bytes:
        pid, aux, start, end = self.after
        return bytes([self.slot, pid, aux, start, end])

    def describe(self) -> list[str]:
        def fmt(e):
            if e is None:
                return "(비어 있음)"
            pid, aux, s, t = e
            name = C.BOX_NAMES.get(pid, f"id{pid}")
            if s >= t:
                return f"{name}: 사용 안 함"
            return (f"{name}: AUX{aux + 1}(RC {aux + 5}번 채널) "
                    f"{step_to_us(s)}~{step_to_us(t)}us")

        return [f"슬롯 {self.slot}", f"  이전: {fmt(self.before)}", f"  이후: {fmt(self.after)}"]


def used_aux(entries: list[tuple[int, int, int, int]], exclude_slot: int | None = None) -> set[int]:
    out = set()
    for i, (pid, aux, s, t) in enumerate(entries):
        if i == exclude_slot or s >= t:
            continue
        out.add(aux)
    return out


def pick_unused_aux(entries, exclude_slot: int | None = None, limit: int = 15) -> int:
    busy = used_aux(entries, exclude_slot)
    for i in range(limit):
        if i not in busy:
            return i
    raise WriteBlocked("빈 AUX 채널을 찾지 못했습니다.")


def plan_angle_always(entries: list[tuple[int, int, int, int]],
                      aux_index: int | None = None,
                      keep_existing: bool = False) -> Plan:
    """ANGLE 모드를 상시 활성으로 만드는 계획.

    전 구간(900~2100us)을 범위로 주면 INAV는 채널 값을 900~2099로 제한해서
    비교하므로 **어떤 값이든 범위 안**이 된다. 수신기가 없어 그 채널이
    존재하지 않아도 항상 켜진다. 이것이 INAV에서 모드를 상시로 두는 방법이다.
    """
    angle_slots = [i for i, (pid, aux, s, t) in enumerate(entries)
                   if pid == BOX_ANGLE and s < t]

    # 이미 전 구간으로 돼 있으면 건드리지 않는다. 그러지 않으면 다시 실행할 때마다
    # '빈 채널'의 정의가 바뀌어 ANGLE이 채널 사이를 왕복한다.
    for i in angle_slots:
        pid, aux, s, t = entries[i]
        if (s, t) == (0, MAX_STEP) and (aux_index is None or aux_index == aux):
            p = Plan(i, entries[i], entries[i], noop=True)
            p.notes.append(
                f"ANGLE이 이미 AUX{aux + 1}(RC {aux + 5}번 채널)에서 전 구간 "
                "상시 활성입니다. 바꿀 것이 없습니다."
            )
            return p

    if keep_existing or not angle_slots:
        empty = [i for i, (pid, aux, s, t) in enumerate(entries) if s >= t]
        if not empty:
            raise WriteBlocked("빈 모드 슬롯이 없습니다.")
        slot = empty[0]
        before = None
    else:
        slot = angle_slots[0]
        before = entries[slot]

    # 대체하는 슬롯의 채널도 '사용 중'으로 본다. 목적이 ANGLE을 실제 스위치
    # 채널에서 떼어내 그 채널을 비우는 것이기 때문이다.
    aux = aux_index if aux_index is not None else pick_unused_aux(entries)
    plan = Plan(slot, before, (BOX_ANGLE, aux, 0, MAX_STEP))
    plan.notes.append(
        f"범위를 {step_to_us(0)}~{step_to_us(MAX_STEP)}us 전 구간으로 두면 "
        "채널 값과 무관하게 항상 활성입니다."
    )
    if before is not None:
        plan.notes.append(
            f"기존 ANGLE 규칙(AUX{before[1] + 1}, "
            f"{step_to_us(before[2])}~{step_to_us(before[3])}us)을 대체하므로 "
            f"RC {before[1] + 5}번 채널이 비워집니다."
        )
    if keep_existing and angle_slots:
        plan.notes.append(
            "기존 규칙을 남겼습니다. 모드 논리가 OR 이므로 둘 중 하나만 맞아도 켜집니다."
        )
    busy = used_aux(entries, slot)
    if aux in busy:
        plan.notes.append(
            f"주의: AUX{aux + 1} 은 다른 모드가 이미 쓰고 있습니다. "
            "상시 활성이라 동작에는 문제 없지만 채널 배치가 겹칩니다."
        )
    return plan


# ---------------------------------------------------------------- 적용


def backup(entries, out_dir="snapshots") -> Path:
    """복구용 기록. INAV CLI 의 aux 명령 형태로도 남긴다."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = d / f"mode_ranges_backup_{stamp}.json"
    lines = []
    for i, (pid, aux, s, t) in enumerate(entries):
        lines.append(f"aux {i} {pid} {aux} {step_to_us(s)} {step_to_us(t)} 0")
    p.write_text(json.dumps({
        "captured_at": stamp,
        "entries": [list(e) for e in entries],
        "cli_restore": lines,
        "note": "복구: INAV CLI 에 cli_restore 줄들을 붙여넣고 save",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def apply(fc, plan: Plan, save: bool = False) -> dict:
    """계획을 적용한다. 반환값에 검증 결과가 담긴다."""
    if is_armed(fc):
        raise WriteBlocked("기체가 무장 상태입니다. 무장을 해제하세요.")

    before = read_entries(fc)
    result: dict = {"backup": str(backup(before)), "saved": False}

    cmd = C.MSP_SET_MODE_RANGE
    _guard(cmd)
    try:
        fc.request(cmd, plan.payload)
    except MspError as e:
        raise WriteBlocked(f"모드 범위 쓰기가 거부됐습니다: {e}") from e

    after = read_entries(fc)
    got = after[plan.slot] if plan.slot < len(after) else None
    result["expected"] = list(plan.after)
    result["actual"] = list(got) if got else None
    result["verified"] = got == plan.after
    if not result["verified"]:
        raise WriteBlocked(
            f"되읽기 검증 실패. 기대 {plan.after}, 실제 {got}. "
            "EEPROM에 저장하지 않았습니다. 전원을 뽑으면 원래 설정으로 돌아갑니다."
        )

    try:
        result["active_modes"] = active_modes(fc)
    except (MspError, KeyError):
        result["active_modes"] = None

    if save:
        _guard(C.MSP_EEPROM_WRITE)
        fc.request(C.MSP_EEPROM_WRITE, b"")
        result["saved"] = True
        result["after_save"] = [list(e) for e in read_entries(fc)]
    return result

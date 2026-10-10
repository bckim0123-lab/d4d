"""INAV/Betaflight 블랙박스 로그(.BBL/.BFL) 디코더.

로그는 ASCII 헤더 + 가변길이 압축 이진 프레임으로 되어 있다. 프레임 종류:

  I  intra(키)프레임. 모든 필드를 예측 없이 또는 상수 예측으로 기록
  P  inter(델타)프레임. 직전 프레임 기준 예측 후 차분만 기록
  S  slow 프레임. 느리게 변하는 값(비행모드 등)
  E  event 프레임
  G  GPS 프레임, H  GPS home 프레임

P 프레임이 직전 값에 의존하므로 한 바이트만 어긋나면 이후 전체가 깨진다.
그래서 이 디코더는 값의 타당성을 계속 검사하고, 깨지면 다음 I 프레임을 찾아
재동기화한다(`Decoded.resyncs`로 보고).

주의: TAG8_4S16 의 16비트 묶음은 빅엔디언이다(니블 패킹 때문). 이 부분은
참조 구현(cleanflight-blackbox-tools)의 v2 변형을 따랐다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

LOG_START = b"H Product:Blackbox flight data recorder"

# 인코딩
ENC_SIGNED_VB = 0
ENC_UNSIGNED_VB = 1
ENC_NEG_14BIT = 3
ENC_TAG8_8SVB = 6
ENC_TAG2_3S32 = 7
ENC_TAG8_4S16 = 8
ENC_NULL = 9
ENC_TAG2_3SVARIABLE = 10

# 예측기
PRED_0 = 0
PRED_PREVIOUS = 1
PRED_STRAIGHT_LINE = 2
PRED_AVERAGE_2 = 3
PRED_MINTHROTTLE = 4
PRED_MOTOR_0 = 5
PRED_INC = 6
PRED_HOME_COORD = 7
PRED_1500 = 8
PRED_VBATREF = 9
PRED_LAST_MAIN_FRAME_TIME = 10
PRED_MINMOTOR = 11

EVENT_SYNC_BEEP = 0
EVENT_INFLIGHT_ADJUSTMENT = 13
EVENT_LOGGING_RESUME = 14
EVENT_FLIGHTMODE = 30
EVENT_IMU_FAILURE = 40
EVENT_LOG_END = 255


class BblError(Exception):
    pass


class _Desync(Exception):
    """프레임 해석이 어긋났다. 재동기화가 필요하다."""


# ---------------------------------------------------------------- 비트 유틸


def _sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & (sign - 1)) - (value & sign)


def _to_i32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _p_interval(text: str) -> tuple[int, int]:
    """'1/2' 또는 '1' 형태를 (num, denom)으로."""
    t = text.strip().split(",")[0]
    if "/" in t:
        a, _, b = t.partition("/")
        try:
            num, den = int(a), int(b)
        except ValueError:
            return 1, 1
        return (num, den) if num > 0 and den > 0 else (1, 1)
    try:
        return (1, 1) if int(t) else (1, 1)
    except ValueError:
        return 1, 1


def _c_div2(v: int) -> int:
    """C의 int32 나눗셈은 0 방향으로 버린다. Python의 // 는 -inf 방향이다."""
    return v // 2 if v >= 0 else -((-v) // 2)


# ---------------------------------------------------------------- 바이트 스트림


class _Stream:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def byte(self) -> int:
        try:
            b = self.buf[self.pos]
        except IndexError:
            raise _Desync("스트림 끝")
        self.pos += 1
        return b

    def peek(self) -> int:
        try:
            return self.buf[self.pos]
        except IndexError:
            raise _Desync("스트림 끝")

    def eof(self) -> bool:
        return self.pos >= len(self.buf)

    def uvb(self) -> int:
        """가변길이 부호없는 정수. 7비트씩, 하위부터, 최상위 비트가 계속 표시."""
        v = 0
        for shift in (0, 7, 14, 21, 28):
            b = self.byte()
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                return v
        raise _Desync("varint가 5바이트를 넘었다")

    def svb(self) -> int:
        """지그재그 부호있는 정수."""
        v = self.uvb()
        return (v >> 1) ^ -(v & 1)


# ---------------------------------------------------------------- 묶음 인코딩


def _tag2_3s32(s: _Stream) -> list[int]:
    """3개 값. 선두 바이트 상위 2비트가 레이아웃을 정한다."""
    lead = s.byte()
    sel = lead >> 6
    if sel == 0:  # 2비트 필드 3개
        return [_sign_extend((lead >> 4) & 3, 2),
                _sign_extend((lead >> 2) & 3, 2),
                _sign_extend(lead & 3, 2)]
    if sel == 1:  # 4비트
        v0 = _sign_extend(lead & 0x0F, 4)
        b = s.byte()
        return [v0, _sign_extend(b >> 4, 4), _sign_extend(b & 0x0F, 4)]
    if sel == 2:  # 6비트
        v0 = _sign_extend(lead & 0x3F, 6)
        v1 = _sign_extend(s.byte() & 0x3F, 6)
        v2 = _sign_extend(s.byte() & 0x3F, 6)
        return [v0, v1, v2]
    # sel == 3: 하위 6비트에 2비트 크기 선택자 3개
    out = []
    size_sel = lead
    for _ in range(3):
        k = size_sel & 3
        if k == 0:
            out.append(_sign_extend(s.byte(), 8))
        elif k == 1:
            out.append(_sign_extend(s.byte() | (s.byte() << 8), 16))
        elif k == 2:
            out.append(_sign_extend(s.byte() | (s.byte() << 8) | (s.byte() << 16), 24))
        else:
            out.append(_sign_extend(s.byte() | (s.byte() << 8) | (s.byte() << 16) | (s.byte() << 24), 32))
        size_sel >>= 2
    return out


TAG8_4S16_NIBBLES = (0, 1, 2, 4)  # 선택자별 니블 수: 0비트 / 4비트 / 8비트 / 16비트


def _tag8_4s16(s: _Stream) -> list[int]:
    """4개 값. 2비트 선택자가 각각 0/4/8/16비트를 지정한다.

    참조 구현은 바이트와 반바이트를 섞어 다루지만, 실제로는 선택자 바이트 다음부터
    **MSB 우선 니블 스트림**을 읽는 것과 완전히 같다. 그래서 니블 커서로 읽는다.
    묶음이 끝날 때 남은 반 니블은 채움값이므로 버린다.
    """
    sel = s.byte()
    base = s.pos
    cur = 0  # 소비한 니블 수
    out = []

    def nib() -> int:
        nonlocal cur
        byte_i = base + (cur >> 1)
        try:
            b = s.buf[byte_i]
        except IndexError:
            raise _Desync("스트림 끝(tag8_4s16)")
        v = (b >> 4) if cur % 2 == 0 else (b & 0x0F)
        cur += 1
        return v

    for _ in range(4):
        count = TAG8_4S16_NIBBLES[sel & 3]
        if count == 0:
            out.append(0)
        else:
            v = 0
            for _ in range(count):
                v = (v << 4) | nib()
            out.append(_sign_extend(v, count * 4))
        sel >>= 2
    s.pos = base + ((cur + 1) >> 1)  # 홀수 니블이면 채움 니블까지 소비
    return out


def _tag8_8svb(s: _Stream, count: int) -> list[int]:
    """최대 8개 값. 1이면 그냥 signed VB, 아니면 헤더 바이트의 비트가 0/비0을 표시."""
    if count == 1:
        return [s.svb()]
    header = s.byte()
    out = []
    for i in range(count):
        out.append(s.svb() if header & (1 << i) else 0)
    return out


# ---------------------------------------------------------------- 헤더


@dataclass
class FrameDef:
    names: list[str] = field(default_factory=list)
    signed: list[int] = field(default_factory=list)
    i_pred: list[int] = field(default_factory=list)
    i_enc: list[int] = field(default_factory=list)
    p_pred: list[int] = field(default_factory=list)
    p_enc: list[int] = field(default_factory=list)


@dataclass
class Header:
    raw: dict[str, str] = field(default_factory=dict)
    main: FrameDef = field(default_factory=FrameDef)
    slow: FrameDef = field(default_factory=FrameDef)
    gps: FrameDef = field(default_factory=FrameDef)
    gps_home: FrameDef = field(default_factory=FrameDef)

    def num(self, key: str, default: float = 0.0) -> float:
        v = self.raw.get(key)
        if v is None:
            return default
        try:
            return float(v.split(",")[0])
        except ValueError:
            return default

    @property
    def firmware(self) -> str:
        return self.raw.get("Firmware revision", self.raw.get("Firmware type", "?"))

    @property
    def craft(self) -> str:
        return self.raw.get("Craft name", "")

    @property
    def gyro_scale(self) -> float:
        """자이로 원시값 -> deg/s 배율."""
        v = self.raw.get("gyro_scale")
        if not v:
            return 1.0
        s = int(v, 16) if v.startswith("0x") else int(v)
        # 펌웨어는 IEEE754 비트패턴을 16진수로 적는다
        import struct as _s

        scale = _s.unpack("<f", _s.pack("<I", s))[0]
        # blackbox_decode와 같은 환산: raw * scale * (1e6/루프주기 보정 없음) * 180/pi
        import math as _m

        return scale * (180.0 / _m.pi) * 1e6 / 1e6 if scale else 1.0


_FIELD_RE = re.compile(r"^Field (\w+) (\w+)$")
_DEF_TARGET = {"I": "main", "P": "main", "S": "slow", "G": "gps", "H": "gps_home"}


def parse_headers(data: bytes, start: int = 0) -> tuple[Header, int]:
    """헤더를 읽고 (헤더, 이진 프레임 시작 위치)를 돌려준다."""
    h = Header()
    pos = start
    n = len(data)
    while pos < n and data[pos : pos + 2] == b"H ":
        end = data.find(b"\n", pos)
        if end < 0:
            end = n
        line = data[pos + 2 : end].decode("latin-1").rstrip("\r")
        pos = end + 1
        key, _, val = line.partition(":")
        h.raw[key] = val
        m = _FIELD_RE.match(key)
        if not m:
            continue
        frame_kind, prop = m.group(1), m.group(2)
        target = _DEF_TARGET.get(frame_kind)
        if target is None:
            continue
        fd: FrameDef = getattr(h, target)
        if prop == "name":
            fd.names = val.split(",")
        elif prop == "signed":
            fd.signed = [int(x) for x in val.split(",")]
        elif prop == "predictor":
            vals = [int(x) for x in val.split(",")]
            if frame_kind == "P":
                fd.p_pred = vals
            else:
                fd.i_pred = vals
        elif prop == "encoding":
            vals = [int(x) for x in val.split(",")]
            if frame_kind == "P":
                fd.p_enc = vals
            else:
                fd.i_enc = vals
    if not h.main.names:
        raise BblError("헤더에서 메인 프레임 필드 정의를 찾지 못했습니다")
    return h, pos


def split_logs(data: bytes) -> list[int]:
    """한 파일에 여러 세션이 이어 붙어 있을 수 있다. 각 시작 오프셋."""
    out = []
    i = data.find(LOG_START)
    while i >= 0:
        out.append(i)
        i = data.find(LOG_START, i + 1)
    return out


# ---------------------------------------------------------------- 프레임 해석


@dataclass
class Decoded:
    header: Header
    names: list[str]
    rows: list[list[int]] = field(default_factory=list)
    frame_counts: dict[str, int] = field(default_factory=dict)
    resyncs: int = 0
    resync_points: list[tuple[int, str]] = field(default_factory=list)
    events: list[tuple[int, int]] = field(default_factory=list)
    truncated: bool = False
    # (메인 프레임 행 번호, 슬로우 프레임 원시값). 예측기 0인 필드만 유효하다.
    slow: list[tuple[int, list[int]]] = field(default_factory=list)

    def slow_col(self, name: str) -> list[tuple[int, int]]:
        """슬로우 프레임 필드를 (행 번호, 값) 목록으로 뽑는다.

        예측기가 0이 아닌 필드는 원시값이 실제값과 다르므로 거부한다.
        """
        fd = self.header.slow
        try:
            k = fd.names.index(name)
        except ValueError:
            return []
        if fd.i_pred[k] != 0:
            raise BblError(
                f"슬로우 필드 {name}은 예측기 {fd.i_pred[k]}를 쓰므로 "
                "원시값을 그대로 읽을 수 없습니다"
            )
        return [(r, v[k]) for r, v in self.slow if k < len(v)]

    def col(self, name: str) -> list[int]:
        try:
            k = self.names.index(name)
        except ValueError:
            return []
        return [r[k] for r in self.rows]

    def segments(self, min_len: int = 64) -> list[tuple[int, int]]:
        """표본 간격이 고른 연속 구간들 [(시작, 끝)).

        재동기화 지점을 건너뛰며 이어붙인 데이터로 FFT를 돌리면 없는 주파수가
        생긴다. 스펙트럼은 반드시 균일 표본 구간에서만 구해야 한다.
        """
        t = self.col("time")
        if len(t) < 2:
            return [(0, len(self.rows))] if self.rows else []
        d = [t[i + 1] - t[i] for i in range(len(t) - 1)]
        pos = sorted(x for x in d if x > 0)
        if not pos:
            return []
        med = pos[len(pos) // 2]
        lo, hi = med * 0.5, med * 3.0
        out = []
        start = 0
        for i, dd in enumerate(d):
            if not (lo <= dd <= hi):
                if i + 1 - start >= min_len:
                    out.append((start, i + 1))
                start = i + 1
        if len(self.rows) - start >= min_len:
            out.append((start, len(self.rows)))
        return out

    def longest_segment(self, min_len: int = 64) -> tuple[int, int]:
        segs = self.segments(min_len)
        if not segs:
            return (0, len(self.rows))
        return max(segs, key=lambda s: s[1] - s[0])

    def slice(self, rng: tuple[int, int]) -> "Decoded":
        d = Decoded(self.header, self.names, self.rows[rng[0]:rng[1]],
                    dict(self.frame_counts), self.resyncs, list(self.resync_points),
                    list(self.events), self.truncated)
        return d

    def has(self, name: str) -> bool:
        return name in self.names

    @property
    def duration_s(self) -> float:
        t = self.col("time")
        return (t[-1] - t[0]) / 1e6 if len(t) > 1 else 0.0


class _MainDecoder:
    """메인(I/P) 프레임 해석기. 예측 이력을 들고 있다."""

    def __init__(self, h: Header):
        self.h = h
        self.fd = h.main
        self.n = len(self.fd.names)
        self.prev: list[int] | None = None
        self.prev2: list[int] | None = None
        self.minthrottle = int(h.num("minthrottle", 1000))
        self.vbatref = int(h.num("vbatref", 0))
        self.motor0 = self.fd.names.index("motor[0]") if "motor[0]" in self.fd.names else -1
        # 로그는 I 간격과 P 간격(num/denom)에 따라 일부 루프를 건너뛴다.
        # loopIteration 의 INC 예측기는 '건너뛴 수 + 1'을 더해야 한다.
        self.interval_i = max(1, int(h.num("I interval", 32)))
        self.p_num, self.p_denom = _p_interval(h.raw.get("P interval", "1/1"))
        self.i_time = self.fd.names.index("time") if "time" in self.fd.names else -1
        self.i_iter = (self.fd.names.index("loopIteration")
                       if "loopIteration" in self.fd.names else -1)

    # -- 원시값 읽기

    def _read_raw(self, s: _Stream, enc: list[int]) -> list[int]:
        raw: list[int] = []
        i = 0
        n = self.n
        while i < n:
            e = enc[i]
            if e == ENC_SIGNED_VB:
                raw.append(s.svb())
                i += 1
            elif e == ENC_UNSIGNED_VB:
                raw.append(s.uvb())
                i += 1
            elif e == ENC_NULL:
                raw.append(0)
                i += 1
            elif e == ENC_NEG_14BIT:
                raw.append(-_sign_extend(s.uvb(), 14))
                i += 1
            elif e == ENC_TAG2_3S32:
                raw.extend(_tag2_3s32(s))
                i += 3
            elif e == ENC_TAG8_4S16:
                raw.extend(_tag8_4s16(s))
                i += 4
            elif e == ENC_TAG8_8SVB:
                cnt = 0
                while cnt < 8 and i + cnt < n and enc[i + cnt] == ENC_TAG8_8SVB:
                    cnt += 1
                raw.extend(_tag8_8svb(s, cnt))
                i += cnt
            elif e == ENC_TAG2_3SVARIABLE:
                raise BblError(
                    "TAG2_3SVARIABLE(10) 인코딩은 아직 구현하지 않았습니다. "
                    "이 로그는 지원되지 않습니다."
                )
            else:
                raise BblError(f"알 수 없는 인코딩 {e} (필드 {self.fd.names[i]})")
        if len(raw) != n:
            raise _Desync(f"필드 수 불일치 {len(raw)} != {n}")
        return raw

    def should_have_frame(self, it: int) -> bool:
        """해당 루프 반복이 기록 대상인지. 참조 구현의 shouldHaveFrame 과 같다."""
        return ((it % self.interval_i) + self.p_num - 1) % self.p_denom < self.p_num

    def _skipped(self, last_it: int) -> int:
        """직전 기록 프레임 다음부터 의도적으로 건너뛴 반복 수."""
        n = 0
        j = last_it + 1
        while not self.should_have_frame(j):
            n += 1
            j += 1
            if n > self.interval_i:  # 안전장치
                return 0
        return n

    # -- 예측 적용

    def _apply(self, raw: list[int], pred: list[int], intra: bool) -> list[int]:
        out = [0] * self.n
        prev, prev2 = self.prev, self.prev2
        for i in range(self.n):
            p = pred[i]
            v = raw[i]
            if p == PRED_0:
                pass
            elif p == PRED_PREVIOUS:
                if prev is not None:
                    v += prev[i]
            elif p == PRED_STRAIGHT_LINE:
                if prev is not None and prev2 is not None:
                    v += 2 * prev[i] - prev2[i]
                elif prev is not None:
                    v += prev[i]
            elif p == PRED_AVERAGE_2:
                if prev is not None and prev2 is not None:
                    v += _c_div2(_to_i32(prev[i] + prev2[i]))
                elif prev is not None:
                    v += prev[i]
            elif p == PRED_MINTHROTTLE or p == PRED_MINMOTOR:
                v += self.minthrottle
            elif p == PRED_1500:
                v += 1500
            elif p == PRED_VBATREF:
                v += self.vbatref
            elif p == PRED_MOTOR_0:
                if self.motor0 >= 0:
                    v += out[self.motor0]
            elif p == PRED_INC:
                v += (prev[i] + self._skipped(prev[i]) + 1) if prev is not None else 0
            else:
                raise BblError(f"알 수 없는 예측기 {p} (필드 {self.fd.names[i]})")
            v &= 0xFFFFFFFF
            out[i] = _to_i32(v) if self.fd.signed[i] else v
        return out

    def intra(self, s: _Stream) -> list[int]:
        # 이력을 지우지 않는다. I 프레임 예측기는 이력을 쓰지 않으므로 결과는 같고,
        # 남겨두면 호출자가 직전 프레임과 비교해 타당성을 검사할 수 있다.
        # (지웠더니 I 프레임마다 검사가 건너뛰어져 동기 이탈을 놓쳤다.)
        raw = self._read_raw(s, self.fd.i_enc)
        return self._apply(raw, self.fd.i_pred, True)

    def inter(self, s: _Stream) -> list[int]:
        if self.prev is None:
            raise _Desync("I 프레임 없이 P 프레임이 나왔다")
        raw = self._read_raw(s, self.fd.p_enc)
        return self._apply(raw, self.fd.p_pred, False)

    def push(self, row: list[int]) -> None:
        self.prev2 = self.prev
        self.prev = row

    def push_intra(self, row: list[int]) -> None:
        """I 프레임 뒤에는 이전·이전2 이력이 모두 그 프레임을 가리킨다.
        펌웨어의 blackboxWriteIntraframe 가 history[1]과 [2]를 같게 두기 때문이다.
        이걸 맞추지 않으면 I 직후의 STRAIGHT_LINE/AVERAGE_2 예측이 틀어진다."""
        self.prev = self.prev2 = row


def _read_simple_frame(s: _Stream, fd: FrameDef, h: Header) -> list[int]:
    """S/G/H 프레임을 읽는다.

    반환값은 **원시값**이다. 예측기를 적용하지 않으므로 예측기가 0이 아닌
    필드의 값은 신뢰할 수 없다. 호출하는 쪽에서 예측기를 확인해야 한다.
    주 용도는 길이를 맞춰 스트림을 전진시키는 것이다.
    """
    if not fd.names:
        raise _Desync("정의 없는 프레임")
    enc = fd.i_enc
    out: list[int] = []
    i, n = 0, len(fd.names)
    while i < n:
        e = enc[i]
        if e in (ENC_SIGNED_VB,):
            out.append(s.svb())
            i += 1
        elif e in (ENC_UNSIGNED_VB, ENC_NEG_14BIT):
            out.append(s.uvb())
            i += 1
        elif e == ENC_NULL:
            out.append(0)
            i += 1
        elif e == ENC_TAG2_3S32:
            out.extend(_tag2_3s32(s))
            i += 3
        elif e == ENC_TAG8_4S16:
            out.extend(_tag8_4s16(s))
            i += 4
        elif e == ENC_TAG8_8SVB:
            cnt = 0
            while cnt < 8 and i + cnt < n and enc[i + cnt] == ENC_TAG8_8SVB:
                cnt += 1
            out.extend(_tag8_8svb(s, cnt))
            i += cnt
        else:
            raise _Desync(f"프레임에서 알 수 없는 인코딩 {e}")
    return out


def _read_event(s: _Stream) -> tuple[int, bool]:
    """(이벤트 코드, 로그 종료 여부)."""
    code = s.byte()
    if code == EVENT_SYNC_BEEP:
        s.uvb()
    elif code == EVENT_FLIGHTMODE:
        s.uvb()
        s.uvb()
    elif code == EVENT_LOGGING_RESUME:
        s.uvb()
        s.uvb()
    elif code == EVENT_IMU_FAILURE:
        s.uvb()
    elif code == EVENT_INFLIGHT_ADJUSTMENT:
        kind = s.byte()
        if kind & 0x80:
            s.byte()
            s.byte()
            s.byte()
            s.byte()
        else:
            s.svb()
    elif code == EVENT_LOG_END:
        # "End of log" 문자열 + 0
        while not s.eof() and s.byte() != 0:
            pass
        return code, True
    else:
        raise _Desync(f"알 수 없는 이벤트 {code}")
    return code, False


def decode(data: bytes, start: int = 0, max_rows: int | None = None,
           max_seconds: float | None = None) -> Decoded:
    """한 세션을 해석한다."""
    h, pos = parse_headers(data, start)
    # 다음 세션 시작 전까지만 본다
    nxt = data.find(LOG_START, pos)
    end = nxt if nxt > 0 else len(data)

    md = _MainDecoder(h)
    out = Decoded(h, list(h.main.names))
    s = _Stream(data, pos)
    counts = out.frame_counts
    t0: int | None = None

    def plausible(row: list[int]) -> bool:
        if md.i_time >= 0:
            t = row[md.i_time]
            if t < 0 or t > 0xFFFFFFFF:
                return False
            if md.prev is not None:
                dt = t - md.prev[md.i_time]
                if dt < 0 or dt > 5_000_000:  # 5초 넘는 점프는 비정상
                    return False
        if md.i_iter >= 0:
            it = row[md.i_iter]
            if it < 0 or (md.prev is not None and it < md.prev[md.i_iter]):
                return False
        return True

    while s.pos < end:
        frame_start = s.pos
        try:
            kind = s.byte()
            if kind == 0x49:  # 'I'
                row = md.intra(s)
                if not plausible(row):
                    raise _Desync("I 프레임 값이 비정상")
                md.push_intra(row)
                out.rows.append(row)
                counts["I"] = counts.get("I", 0) + 1
            elif kind == 0x50:  # 'P'
                row = md.inter(s)
                if not plausible(row):
                    raise _Desync("P 프레임 값이 비정상")
                md.push(row)
                out.rows.append(row)
                counts["P"] = counts.get("P", 0) + 1
            elif kind == 0x53:  # 'S'
                out.slow.append((len(out.rows), _read_simple_frame(s, h.slow, h)))
                counts["S"] = counts.get("S", 0) + 1
            elif kind == 0x47:  # 'G'
                _read_simple_frame(s, h.gps, h)
                counts["G"] = counts.get("G", 0) + 1
            elif kind == 0x48:  # 'H'
                _read_simple_frame(s, h.gps_home, h)
                counts["H"] = counts.get("H", 0) + 1
            elif kind == 0x45:  # 'E'
                code, done = _read_event(s)
                out.events.append((code, len(out.rows)))
                counts["E"] = counts.get("E", 0) + 1
                if done:
                    break
            else:
                raise _Desync(f"알 수 없는 프레임 종류 0x{kind:02X}")
        except (_Desync, IndexError) as e:
            out.resyncs += 1
            if len(out.resync_points) < 50:
                out.resync_points.append((frame_start, f"{e} (행 {len(out.rows)})"))
            found = _resync(data, max(frame_start + 1, s.pos - 64), end, md,
                            out.rows[-1] if out.rows else None)
            if found is None:
                out.truncated = True
                break
            np, row = found
            s = _Stream(data, np)
            md.push_intra(row)
            out.rows.append(row)
            counts["I"] = counts.get("I", 0) + 1
            continue

        if out.rows:
            if max_rows is not None and len(out.rows) >= max_rows:
                out.truncated = True
                break
            if max_seconds is not None and md.i_time >= 0:
                t = out.rows[-1][md.i_time]
                if t0 is None:
                    t0 = t
                elif (t - t0) / 1e6 >= max_seconds:
                    out.truncated = True
                    break
    return out


RESYNC_SPAN = 1 << 16  # 재동기화를 포기하기까지 훑어볼 바이트 수


def _resync(data: bytes, frm: int, end: int, md: "_MainDecoder",
            last: list[int] | None) -> tuple[int, list[int]] | None:
    """다음으로 **타당한** I 프레임을 찾는다.

    0x49('I')는 데이터 바이트로도 흔히 나오므로 위치만 보고 믿으면 안 된다.
    실제로 해석해 보고, 직전 정상 프레임과 시간·반복수가 앞뒤로 맞는지까지
    확인한다. 그래야 복구한 구간을 신뢰할 수 있다.
    """
    period = md.h.num("looptime", 0.0) or 0.0
    limit = min(end, frm + RESYNC_SPAN)
    i = frm
    while True:
        i = data.find(b"I", i, limit)
        if i < 0:
            return None
        probe = _Stream(data, i + 1)
        saved = (md.prev, md.prev2)
        md.prev = md.prev2 = None
        try:
            row = md.intra(probe)
        except (_Desync, IndexError, BblError):
            row = None
        finally:
            md.prev, md.prev2 = saved
        if row is not None and _accept(row, last, md, period):
            return probe.pos, row
        i += 1


def _accept(row: list[int], last: list[int] | None, md: "_MainDecoder",
            period: float) -> bool:
    if md.i_time < 0 or md.i_iter < 0:
        return True
    t, it = row[md.i_time], row[md.i_iter]
    if t < 0 or it < 0:
        return False
    if last is None:
        return True
    dt, di = t - last[md.i_time], it - last[md.i_iter]
    if dt <= 0 or di <= 0:
        return False
    if period > 0:
        # 시간 증가와 반복수 증가가 루프 주기로 서로 설명돼야 한다
        ratio = dt / (di * period)
        if not 0.3 <= ratio <= 3.0:
            return False
    return True


def decode_file(path, max_rows: int | None = None, max_seconds: float | None = None,
                session: int = 0) -> Decoded:
    with open(path, "rb") as f:
        data = f.read()
    starts = split_logs(data)
    if not starts:
        raise BblError(
            "블랙박스 헤더를 찾지 못했습니다. .BBL/.BFL 파일인지 확인하세요."
        )
    if session >= len(starts):
        raise BblError(f"세션 {session}가 없습니다. 이 파일에는 {len(starts)}개 있습니다.")
    return decode(data, starts[session], max_rows, max_seconds)


def sessions(path) -> list[dict]:
    """파일 안의 세션 목록을 가볍게 훑는다."""
    with open(path, "rb") as f:
        data = f.read()
    out = []
    starts = split_logs(data)
    for i, st in enumerate(starts):
        try:
            h, pos = parse_headers(data, st)
        except BblError:
            continue
        nxt = starts[i + 1] if i + 1 < len(starts) else len(data)
        out.append({
            "index": i,
            "offset": st,
            "bytes": nxt - st,
            "craft": h.craft,
            "firmware": h.firmware,
            "fields": len(h.main.names),
        })
    return out

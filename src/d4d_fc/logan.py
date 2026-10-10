"""블랙박스 로그 분석: 자이로 스펙트럼, 대역 에너지, 증상 분류.

표준 라이브러리만 쓴다. FFT는 radix-2 Cooley-Tukey를 직접 구현했고,
스펙트럼은 Hann 창을 겹쳐 평균하는 Welch 방식이다.

INAV는 `gyroADC[]`에 필터를 거친 자이로를 **deg/s 정수**로 기록한다
(`gyro.gyroADCf`를 lrintf 한 값). Betaflight는 원시 센서값이라 배율이 필요하다.
여기서는 헤더의 펌웨어 종류를 보고 판단하고, 확실하지 않으면 단위를 밝히지 않는다.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

from .bbl import Decoded

# 분석 대역 (Hz). 경계는 멀티로터 튜닝에서 통용되는 구간이다.
BANDS = [
    ("control", 0.0, 10.0, "조종 입력과 기체 응답. 여기 에너지는 정상"),
    ("wobble", 1.0, 5.0, "저주파 요동. 크면 P/I 과다"),
    ("mid", 10.0, 40.0, "중간 대역. 프롭워시와 자세 진동"),
    ("high", 80.0, 200.0, "프레임·모터 진동. D텀이 증폭하는 구간"),
    ("noise", 200.0, 1000.0, "순수 노이즈. 필터로 막아야 함"),
]

# 판정 기준값 (deg/s RMS). 로그 하나로 보정한 값이 아니라 통용되는 경험치다.
TH_HIGH_RMS = 8.0      # 80~200Hz RMS가 이보다 크면 고주파 문제
TH_NOISE_RMS = 5.0     # 200Hz+ RMS
TH_WOBBLE_RMS = 12.0   # 1~5Hz RMS
TH_PEAK_HZ = 60.0      # 이 위의 봉우리는 기계적 노이즈로 본다
TH_PROPWASH_RATIO = 1.6  # 저스로틀 구간 노이즈 / 고스로틀 구간 노이즈


# ---------------------------------------------------------------- FFT


def _fft(a: list[complex]) -> list[complex]:
    n = len(a)
    if n & (n - 1):
        raise ValueError("길이가 2의 거듭제곱이어야 합니다")
    # 비트 역순 정렬
    j = 0
    a = list(a)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    while length <= n:
        step = -2j * cmath.pi / length
        wl = cmath.exp(step)
        half = length >> 1
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * w
                a[i + k] = u + v
                a[i + k + half] = u - v
                w *= wl
        length <<= 1
    return a


def _hann(n: int) -> list[float]:
    if n == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]


def spectrum(x: list[float], fs: float, nfft: int = 1024) -> tuple[list[float], list[float]]:
    """(주파수, 진폭 스펙트럼 밀도의 제곱근) — 단위는 입력 단위/sqrt(Hz) 가 아니라
    창별 평균 진폭이다. 대역 RMS 계산에 쓰기 위해 파워를 보존한다."""
    if len(x) < nfft:
        nfft = 1 << max(3, int(math.log2(max(len(x), 8))))
    if len(x) < nfft:
        return [], []
    win = _hann(nfft)
    # 창 파워 보정
    wsum2 = sum(w * w for w in win)
    hop = nfft // 2
    acc = [0.0] * (nfft // 2 + 1)
    count = 0
    mean = sum(x) / len(x)
    pos = 0
    while pos + nfft <= len(x):
        seg = [(x[pos + i] - mean) * win[i] for i in range(nfft)]
        spec = _fft([complex(v, 0.0) for v in seg])
        for k in range(nfft // 2 + 1):
            acc[k] += abs(spec[k]) ** 2
        count += 1
        pos += hop
    if not count:
        return [], []
    # 단측 파워로 정규화. 파르세발: sum_k |X_k|^2 = N * sum_n |x_n w_n|^2 이므로
    # 분산을 복원하려면 N(=nfft)과 창 파워로 모두 나눠야 한다. 그러면
    # 전 대역 합이 신호 분산이 되고 band_rms가 실제 RMS를 돌려준다.
    denom = wsum2 * nfft
    power = []
    for k in range(nfft // 2 + 1):
        p = acc[k] / count / denom
        if 0 < k < nfft // 2:
            p *= 2.0
        power.append(p)
    freqs = [k * fs / nfft for k in range(nfft // 2 + 1)]
    return freqs, power


def band_rms(freqs: list[float], power: list[float], lo: float, hi: float) -> float:
    """대역 안의 RMS. power는 단측 파워 스펙트럼."""
    s = 0.0
    for f, p in zip(freqs, power):
        if lo <= f < hi:
            s += p
    return math.sqrt(s)


def peaks(freqs: list[float], power: list[float], fmin: float = 5.0,
          top: int = 5) -> list[tuple[float, float]]:
    """(주파수, 진폭) 상위 봉우리. 국소 최대만 고른다."""
    out = []
    for i in range(1, len(power) - 1):
        if freqs[i] < fmin:
            continue
        if power[i] >= power[i - 1] and power[i] >= power[i + 1]:
            out.append((freqs[i], math.sqrt(power[i])))
    out.sort(key=lambda t: -t[1])
    return out[:top]


# ---------------------------------------------------------------- 분석


@dataclass
class AxisStats:
    axis: str
    rms: float
    bands: dict[str, float] = field(default_factory=dict)
    top_peaks: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Analysis:
    sample_rate_hz: float
    duration_s: float
    rows: int
    gyro_unit: str
    axes: list[AxisStats] = field(default_factory=list)
    motor_mean: float | None = None
    motor_max: int | None = None
    motor_saturation_pct: float | None = None
    throttle_mean: float | None = None
    pid_rms: dict[str, float] = field(default_factory=dict)
    propwash_ratio: float | None = None
    segment: tuple[int, int] = (0, 0)
    total_rows: int = 0
    airborne: bool = True
    acc_vib: float | None = None
    symptom: str | None = None
    confidence: str = "낮음"
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


AXES = ("roll", "pitch", "yaw")


def _sample_rate(times: list[int]) -> float:
    if len(times) < 3:
        return 0.0
    d = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
    med = d[len(d) // 2]
    return 1e6 / med if med > 0 else 0.0


def _gyro_unit(d: Decoded) -> str:
    fw = (d.header.raw.get("Firmware revision", "") or "").upper()
    if "INAV" in fw:
        return "deg/s"
    if "BETAFLIGHT" in fw or "BTFL" in fw:
        return "원시 센서값"
    return "단위 불명"


def analyze(d: Decoded, nfft: int = 1024) -> Analysis:
    # 스펙트럼은 균일 표본 구간에서만 유효하다. 손상이나 기록 중단으로 생긴
    # 틈을 가로질러 FFT를 돌리면 실제로 없는 주파수가 생긴다.
    segs = d.segments()
    total = len(d.rows)
    if segs:
        rng = max(segs, key=lambda s: s[1] - s[0])
        if rng[1] - rng[0] < total:
            d = d.slice(rng)
    else:
        rng = (0, total)

    times = d.col("time")
    fs = _sample_rate(times)
    unit = _gyro_unit(d)
    a = Analysis(sample_rate_hz=fs, duration_s=d.duration_s, rows=len(d.rows), gyro_unit=unit)
    a.segment = rng
    a.total_rows = total
    if len(segs) > 1 or len(d.rows) < total:
        a.warnings.append(
            f"표본 {total}개 중 간격이 고른 최장 구간 {len(d.rows)}개"
            f"(행 {rng[0]}~{rng[1]})만 분석했습니다. 로그에 {max(0, len(segs) - 1)}개의 "
            "틈이나 손상이 있어 그대로 이어붙이면 스펙트럼이 왜곡됩니다."
        )

    if fs <= 0:
        a.warnings.append("샘플 주기를 구할 수 없어 주파수 분석을 못 했습니다.")
        return a
    if len(d.rows) < nfft:
        a.warnings.append(
            f"표본이 {len(d.rows)}개뿐이어서 {nfft}점 FFT를 못 합니다. 더 긴 로그가 필요합니다."
        )

    for i, name in enumerate(AXES):
        col = d.col(f"gyroADC[{i}]")
        if not col:
            continue
        x = [float(v) for v in col]
        mean = sum(x) / len(x)
        rms = math.sqrt(sum((v - mean) ** 2 for v in x) / len(x))
        st = AxisStats(name, rms)
        freqs, power = spectrum(x, fs, nfft)
        if freqs:
            for bn, lo, hi, _ in BANDS:
                st.bands[bn] = band_rms(freqs, power, lo, min(hi, fs / 2))
            st.top_peaks = peaks(freqs, power)
        a.axes.append(st)

    # 모터 포화와 실제 비행 여부
    motors = [d.col(f"motor[{i}]") for i in range(4)]
    motors = [m for m in motors if m]
    if motors:
        allv = [v for m in motors for v in m]
        a.motor_mean = sum(allv) / len(allv)
        a.motor_max = max(allv)
        hi = a.motor_max
        a.motor_saturation_pct = 100.0 * sum(1 for v in allv if v >= hi - 2) / len(allv)
        a.airborne = _airborne(d, a, motors)
        if not a.airborne:
            a.warnings.append(
                f"이 구간에서 모터가 거의 돌지 않았습니다(최대 {a.motor_max}, "
                f"공회전 기준 {int(d.header.num('minthrottle', 1000))}). 날지 않은 로그이므로 "
                "자이로 움직임은 기체를 손으로 움직인 것입니다. PID 판정을 하지 않습니다."
            )

    rc3 = d.col("rcCommand[3]")
    if rc3:
        a.throttle_mean = sum(rc3) / len(rc3)

    for term in ("P", "I", "D", "F"):
        vals = []
        for i in range(3):
            c = d.col(f"axis{term}[{i}]")
            if c:
                vals.extend(float(v) for v in c)
        if vals:
            m = sum(vals) / len(vals)
            a.pid_rms[term] = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))

    a.propwash_ratio = _propwash(d, fs, nfft)
    _classify(a)
    return a


def _propwash(d: Decoded, fs: float, nfft: int) -> float | None:
    """저스로틀(하강) 구간과 고스로틀 구간의 중간대역 노이즈 비.
    프롭워시는 하강 중에만 심해지므로 비가 크면 그 증상이다."""
    thr = d.col("rcCommand[3]")
    gy = d.col("gyroADC[0]")
    gy1 = d.col("gyroADC[1]")
    if not thr or not gy or fs <= 0:
        return None
    win = min(nfft, 512)
    if len(thr) < win * 4:
        return None
    segs = []
    for pos in range(0, len(thr) - win, win):
        t = sum(thr[pos:pos + win]) / win
        x = [float(v) for v in gy[pos:pos + win]]
        y = [float(v) for v in gy1[pos:pos + win]] if gy1 else x
        f1, p1 = spectrum(x, fs, win)
        f2, p2 = spectrum(y, fs, win)
        if not f1:
            continue
        e = band_rms(f1, p1, 10.0, 40.0) + band_rms(f2, p2, 10.0, 40.0)
        segs.append((t, e))
    if len(segs) < 6:
        return None
    segs.sort(key=lambda s: s[0])
    k = max(1, len(segs) // 3)
    low = sum(e for _, e in segs[:k]) / k
    high = sum(e for _, e in segs[-k:]) / k
    return (low / high) if high > 0 else None


def _airborne(d: Decoded, a: Analysis, motors: list[list[int]]) -> bool:
    """모터가 실제로 돌았는지. 손으로 흔든 벤치 로그를 진동 문제로 오진하지 않으려면
    반드시 먼저 확인해야 한다."""
    idle = d.header.num("minthrottle", 1000.0)
    top = d.header.num("maxthrottle", 2000.0)
    thresh = idle + max(20.0, 0.05 * (top - idle))
    per_row = [max(m[i] for m in motors) for i in range(len(motors[0]))]
    above = sum(1 for v in per_row if v > thresh)
    vib = d.col("accVib")
    if vib:
        a.acc_vib = sum(vib) / len(vib)
    # 절반 이상의 표본에서 모터가 공회전 위로 올라가야 비행으로 본다
    if above < 0.5 * len(per_row):
        return False
    # 모터는 돌았지만 가속도 진동이 사실상 없으면 프로펠러가 없는 상태다
    if a.acc_vib is not None and a.acc_vib < 50:
        return False
    return True


def _classify(a: Analysis) -> None:
    """대역 에너지로 증상을 고른다. 경험적 기준이며 확정 진단이 아니다."""
    if not a.axes:
        a.warnings.append("gyroADC 필드가 없어 증상을 판정할 수 없습니다.")
        return
    if not a.airborne:
        return
    if a.gyro_unit != "deg/s":
        a.warnings.append(
            f"자이로 단위가 '{a.gyro_unit}'이라 절대 기준값을 적용할 수 없습니다. "
            "봉우리 주파수만 참고하세요."
        )

    worst = max(a.axes, key=lambda s: s.bands.get("high", 0.0))
    high = worst.bands.get("high", 0.0)
    noise = worst.bands.get("noise", 0.0)
    wob = max(s.bands.get("wobble", 0.0) for s in a.axes)
    top = worst.top_peaks[0] if worst.top_peaks else None

    for s in a.axes:
        if s.top_peaks:
            f, mag = s.top_peaks[0]
            a.findings.append(
                f"{s.axis}: RMS {s.rms:.1f}, 최대 봉우리 {f:.0f}Hz, "
                f"대역 RMS 1~5Hz {s.bands.get('wobble', 0):.1f} / "
                f"80~200Hz {s.bands.get('high', 0):.1f} / 200Hz+ {s.bands.get('noise', 0):.1f}"
            )

    if a.gyro_unit != "deg/s":
        if top and top[0] >= TH_PEAK_HZ:
            a.symptom, a.confidence = "highfreq", "낮음"
        return

    if high >= TH_HIGH_RMS or noise >= TH_NOISE_RMS:
        a.symptom = "highfreq"
        a.confidence = "높음" if high >= TH_HIGH_RMS * 1.5 else "보통"
        a.findings.append(
            f"{worst.axis} 축의 80~200Hz RMS가 {high:.1f}로 기준({TH_HIGH_RMS})을 넘습니다. "
            "프레임·프롭 진동이 D텀으로 들어오고 있습니다."
        )
        return
    if a.propwash_ratio is not None and a.propwash_ratio >= TH_PROPWASH_RATIO:
        a.symptom = "propwash"
        a.confidence = "보통"
        a.findings.append(
            f"저스로틀 구간의 10~40Hz 노이즈가 고스로틀 구간의 "
            f"{a.propwash_ratio:.1f}배입니다. 하강 중에만 흔들리는 양상입니다."
        )
        return
    if wob >= TH_WOBBLE_RMS:
        a.symptom = "wobble"
        a.confidence = "보통"
        a.findings.append(f"1~5Hz RMS가 {wob:.1f}로 기준({TH_WOBBLE_RMS})을 넘습니다.")
        return
    a.symptom = None
    a.confidence = "보통"
    a.findings.append(
        "모든 대역이 기준값 아래입니다. 이 로그에서는 뚜렷한 진동 문제가 보이지 않습니다."
    )


# ---------------------------------------------------------------- 보고


def render(d: Decoded, a: Analysis) -> list[str]:
    h = d.header
    L = ["# 블랙박스 로그 분석", ""]
    L.append(f"- 기체: {h.craft or '(이름 없음)'}")
    L.append(f"- 펌웨어: {h.firmware}")
    L.append(f"- 표본 {a.rows}개, {a.duration_s:.1f}초, 기록율 {a.sample_rate_hz:.0f}Hz")
    if a.total_rows and a.rows != a.total_rows:
        L.append(f"- 해석한 전체 표본 {a.total_rows}개 중 균일 구간 "
                 f"{a.segment[0]}~{a.segment[1]} 를 분석")
    L.append(f"- 자이로 단위: {a.gyro_unit}")
    fc = ", ".join(f"{k}={v}" for k, v in sorted(d.frame_counts.items()))
    L.append(f"- 프레임: {fc}")
    if d.resyncs:
        L.append(f"- **재동기화 {d.resyncs}회** (로그 손상 또는 디코더 한계). "
                 "횟수가 많으면 아래 수치를 믿지 마세요.")
        for off, why in d.resync_points[:5]:
            L.append(f"  - 오프셋 {off} (0x{off:X}, 512섹터 경계까지 {512 - off % 512}B): {why}")
    if d.truncated:
        L.append("- 분석 한도에서 끊었습니다(전체를 보려면 `--seconds 0`)")
    L.append("")

    if a.axes:
        L.append("## 자이로 대역별 RMS")
        L.append("")
        L.append("| 축 | 전체 RMS | 1~5Hz | 10~40Hz | 80~200Hz | 200Hz+ | 최대 봉우리 |")
        L.append("|---|---|---|---|---|---|---|")
        for s in a.axes:
            pk = f"{s.top_peaks[0][0]:.0f}Hz" if s.top_peaks else "-"
            L.append(f"| {s.axis} | {s.rms:.1f} | {s.bands.get('wobble', 0):.1f} | "
                     f"{s.bands.get('mid', 0):.1f} | {s.bands.get('high', 0):.1f} | "
                     f"{s.bands.get('noise', 0):.1f} | {pk} |")
        L.append("")
        L.append("## 봉우리 상세")
        L.append("")
        for s in a.axes:
            if s.top_peaks:
                pk = ", ".join(f"{f:.0f}Hz({m:.1f})" for f, m in s.top_peaks)
                L.append(f"- {s.axis}: {pk}")
        L.append("")

    L.append("## 출력과 PID 항")
    L.append("")
    if a.motor_mean is not None:
        L.append(f"- 모터 평균 {a.motor_mean:.0f}, 최대 {a.motor_max}, "
                 f"최대치 근접 비율 {a.motor_saturation_pct:.1f}%")
        L.append(f"- 실제 비행: {'예' if a.airborne else '**아니오 (날지 않은 로그)**'}")
    if a.acc_vib is not None:
        L.append(f"- accVib 평균 {a.acc_vib:.0f}")
    if a.throttle_mean is not None:
        L.append(f"- 스로틀 평균 {a.throttle_mean:.0f}")
    if a.pid_rms:
        L.append("- PID 항 RMS: " + ", ".join(f"{k}={v:.1f}" for k, v in a.pid_rms.items()))
    if a.propwash_ratio is not None:
        L.append(f"- 저스로틀/고스로틀 중간대역 노이즈 비: {a.propwash_ratio:.2f}")
    L.append("")

    if a.findings:
        L.append("## 관측")
        L.append("")
        for f in a.findings:
            L.append(f"- {f}")
        L.append("")
    if a.warnings:
        L.append("## 주의")
        L.append("")
        for w in a.warnings:
            L.append(f"- {w}")
        L.append("")

    L.append("## 판정")
    L.append("")
    if a.symptom:
        L.append(f"- 증상: **{a.symptom}** (확신도 {a.confidence})")
        L.append(f"- 변경안: `python -m d4d_fc tune --symptom {a.symptom}`")
    else:
        L.append("- 뚜렷한 증상이 잡히지 않았습니다. PID를 바꾸기 전에 다른 원인을 보세요.")
    L.append("")
    L.append("기준값은 멀티로터 튜닝에서 통용되는 경험치이고 이 기체로 보정한 값이 "
             "아닙니다. 봉우리 주파수와 대역 RMS를 직접 보고 판단하세요.")
    return L

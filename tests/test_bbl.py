"""블랙박스 디코더와 로그 분석 테스트.

한계를 분명히 해둔다: 아래 왕복 시험은 **이 구현의 내부 일관성**을 검증한다.
INAV가 실제로 쓰는 바이트 배치와 일치하는지는 진짜 .BBL 파일로만 확인할 수 있다.
그래서 구조 검증(loopIteration 증가, time 단조증가, 모터 범위)을 함께 둬서
실제 파일에서 어긋남이 생기면 조용히 넘어가지 않고 드러나게 했다.
"""
from __future__ import annotations

import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4d_fc import bbl  # noqa: E402
from d4d_fc.logan import analyze, band_rms, spectrum  # noqa: E402

# ---------------------------------------------------------------- 인코더 (시험용)


def uvb(v: int) -> bytes:
    assert v >= 0
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def svb(v: int) -> bytes:
    return uvb((v << 1) if v >= 0 else (((-v) << 1) - 1))


def _fits(v: int, bits: int) -> bool:
    return -(1 << (bits - 1)) <= v < (1 << (bits - 1))


def enc_tag2_3s32(vals: list[int]) -> bytes:
    assert len(vals) == 3
    if all(_fits(v, 2) for v in vals):
        lead = ((vals[0] & 3) << 4) | ((vals[1] & 3) << 2) | (vals[2] & 3)
        return bytes([lead])
    if all(_fits(v, 4) for v in vals):
        return bytes([0x40 | (vals[0] & 0x0F), ((vals[1] & 0x0F) << 4) | (vals[2] & 0x0F)])
    if all(_fits(v, 6) for v in vals):
        return bytes([0x80 | (vals[0] & 0x3F), vals[1] & 0x3F, vals[2] & 0x3F])
    sizes, body = [], bytearray()
    for v in vals:
        if _fits(v, 8):
            sizes.append(0)
            body += struct.pack("<b", v)
        elif _fits(v, 16):
            sizes.append(1)
            body += struct.pack("<h", v)
        elif _fits(v, 24):
            sizes.append(2)
            body += (v & 0xFFFFFF).to_bytes(3, "little")
        else:
            sizes.append(3)
            body += struct.pack("<i", v)
    lead = 0xC0 | sizes[0] | (sizes[1] << 2) | (sizes[2] << 4)
    return bytes([lead]) + bytes(body)


def enc_tag8_4s16(vals: list[int]) -> bytes:
    """MSB 우선 니블 스트림으로 쓴다."""
    assert len(vals) == 4
    sel = 0
    nibbles: list[int] = []
    for i, v in enumerate(vals):
        if v == 0:
            k = 0
        elif _fits(v, 4):
            k = 1
        elif _fits(v, 8):
            k = 2
        else:
            k = 3
        sel |= k << (2 * i)
        count = bbl.TAG8_4S16_NIBBLES[k]
        if count:
            masked = v & ((1 << (count * 4)) - 1)
            for sh in range(count - 1, -1, -1):
                nibbles.append((masked >> (sh * 4)) & 0x0F)
    if len(nibbles) % 2:
        nibbles.append(0)
    body = bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))
    return bytes([sel]) + body


def enc_tag8_8svb(vals: list[int]) -> bytes:
    if len(vals) == 1:
        return svb(vals[0])
    header = 0
    body = bytearray()
    for i, v in enumerate(vals):
        if v != 0:
            header |= 1 << i
            body += svb(v)
    return bytes([header]) + bytes(body)


# ---------------------------------------------------------------- 합성 로그

FIELDS = (["loopIteration", "time"]
          + [f"axisP[{i}]" for i in range(3)]
          + [f"gyroADC[{i}]" for i in range(3)]
          + [f"motor[{i}]" for i in range(4)]
          + [f"rcCommand[{i}]" for i in range(4)])

SIGNED = [0, 0] + [1] * 3 + [1] * 3 + [0] * 4 + [1, 1, 1, 0]
I_PRED = ([bbl.PRED_0, bbl.PRED_0] + [bbl.PRED_0] * 3 + [bbl.PRED_0] * 3
          + [bbl.PRED_MINTHROTTLE] + [bbl.PRED_MOTOR_0] * 3
          + [bbl.PRED_0] * 3 + [bbl.PRED_MINTHROTTLE])
I_ENC = ([bbl.ENC_UNSIGNED_VB, bbl.ENC_UNSIGNED_VB] + [bbl.ENC_SIGNED_VB] * 3
         + [bbl.ENC_SIGNED_VB] * 3
         + [bbl.ENC_UNSIGNED_VB] + [bbl.ENC_SIGNED_VB] * 3
         + [bbl.ENC_SIGNED_VB] * 3 + [bbl.ENC_UNSIGNED_VB])
P_PRED = ([bbl.PRED_INC, bbl.PRED_STRAIGHT_LINE] + [bbl.PRED_PREVIOUS] * 3
          + [bbl.PRED_PREVIOUS] * 3 + [bbl.PRED_AVERAGE_2] * 4
          + [bbl.PRED_PREVIOUS] * 4)
P_ENC = ([bbl.ENC_NULL, bbl.ENC_SIGNED_VB] + [bbl.ENC_SIGNED_VB] * 3
         + [bbl.ENC_TAG2_3S32] * 3 + [bbl.ENC_TAG8_8SVB] * 4
         + [bbl.ENC_TAG8_4S16] * 4)

MINTHROTTLE = 1000


def _headers() -> bytes:
    def j(v):
        return ",".join(str(x) for x in v)

    lines = [
        b"H Product:Blackbox flight data recorder by Nicholas Sherlock",
        b"H Data version:2",
        b"H Firmware type:Cleanflight",
        b"H Firmware revision:INAV 9.1.0 (abc1234) MICOAIR743V2",
        b"H Craft name:TESTBENCH",
        b"H minthrottle:%d" % MINTHROTTLE,
        b"H vbatref:0",
        b"H I interval:32",
        b"H P interval:1/1",
        ("H Field I name:" + ",".join(FIELDS)).encode(),
        ("H Field I signed:" + j(SIGNED)).encode(),
        ("H Field I predictor:" + j(I_PRED)).encode(),
        ("H Field I encoding:" + j(I_ENC)).encode(),
        ("H Field P predictor:" + j(P_PRED)).encode(),
        ("H Field P encoding:" + j(P_ENC)).encode(),
        b"H Field S name:flightModeFlags,stateFlags,failsafePhase",
        b"H Field S signed:0,0,0",
        b"H Field S predictor:0,0,0",
        b"H Field S encoding:1,1,1",
    ]
    return b"\n".join(lines) + b"\n"


def _encode_i(row: list[int]) -> bytes:
    out = bytearray(b"I")
    for i, v in enumerate(row):
        e, p = I_ENC[i], I_PRED[i]
        raw = v
        if p == bbl.PRED_MINTHROTTLE:
            raw = v - MINTHROTTLE
        elif p == bbl.PRED_MOTOR_0:
            raw = v - row[FIELDS.index("motor[0]")]
        out += uvb(raw) if e == bbl.ENC_UNSIGNED_VB else svb(raw)
    return bytes(out)


def _encode_p(row: list[int], prev: list[int], prev2: list[int]) -> bytes:
    out = bytearray(b"P")
    raws = []
    for i, v in enumerate(row):
        p = P_PRED[i]
        if p == bbl.PRED_INC:
            pred = prev[i] + 1
        elif p == bbl.PRED_PREVIOUS:
            pred = prev[i]
        elif p == bbl.PRED_STRAIGHT_LINE:
            pred = 2 * prev[i] - prev2[i]
        elif p == bbl.PRED_AVERAGE_2:
            s = prev[i] + prev2[i]
            pred = s // 2 if s >= 0 else -((-s) // 2)
        else:
            pred = 0
        raws.append(v - pred)
    i = 0
    while i < len(raws):
        e = P_ENC[i]
        if e == bbl.ENC_NULL:
            assert raws[i] == 0, "NULL 인코딩 필드는 예측과 정확히 일치해야 한다"
            i += 1
        elif e == bbl.ENC_SIGNED_VB:
            out += svb(raws[i])
            i += 1
        elif e == bbl.ENC_UNSIGNED_VB:
            out += uvb(raws[i])
            i += 1
        elif e == bbl.ENC_TAG2_3S32:
            out += enc_tag2_3s32(raws[i:i + 3])
            i += 3
        elif e == bbl.ENC_TAG8_4S16:
            out += enc_tag8_4s16(raws[i:i + 4])
            i += 4
        elif e == bbl.ENC_TAG8_8SVB:
            n = 0
            while n < 8 and i + n < len(raws) and P_ENC[i + n] == bbl.ENC_TAG8_8SVB:
                n += 1
            out += enc_tag8_8svb(raws[i:i + n])
            i += n
        else:
            raise AssertionError(e)
    return bytes(out)


def make_log(rows: list[list[int]], i_interval: int = 32) -> bytes:
    out = bytearray(_headers())
    prev = prev2 = None
    for n, row in enumerate(rows):
        if n % i_interval == 0 or prev is None:
            out += _encode_i(row)
            # 펌웨어와 같게: I 프레임 뒤에는 이전과 이전2가 모두 이 프레임이다
            prev = prev2 = row
        else:
            out += _encode_p(row, prev, prev2)
            prev2, prev = prev, row
        if n % 64 == 63:  # 중간에 S 프레임을 끼워 동기화 유지를 확인
            out += b"S" + uvb(5) + uvb(1) + uvb(0)
    out += b"E" + bytes([255]) + b"End of log\x00"
    return bytes(out)


def synth_rows(n: int, fs: float, freqs_amps: dict[int, list[tuple[float, float]]],
               throttle: int = 1400) -> list[list[int]]:
    """축별로 (주파수, 진폭) 성분을 합성한 자이로 데이터를 만든다."""
    rows = []
    dt = int(round(1e6 / fs))
    for k in range(n):
        t = k * dt
        gy = []
        for ax in range(3):
            v = 0.0
            for f, amp in freqs_amps.get(ax, []):
                v += amp * math.sin(2 * math.pi * f * (t / 1e6))
            gy.append(int(round(v)))
        row = [k, t]
        row += [gy[0] // 3, gy[1] // 3, gy[2] // 3]          # axisP
        row += gy                                             # gyroADC
        row += [throttle + gy[0] // 8, throttle - gy[0] // 8,
                throttle + gy[1] // 8, throttle - gy[1] // 8]  # motor
        row += [gy[0] // 10, gy[1] // 10, gy[2] // 10, throttle]  # rcCommand
        rows.append(row)
    return rows


# ---------------------------------------------------------------- 원시 인코딩


class PrimitiveTests(unittest.TestCase):
    def test_uvb_known_vectors(self):
        for v, enc in [(0, b"\x00"), (1, b"\x01"), (127, b"\x7f"),
                       (128, b"\x80\x01"), (300, b"\xac\x02"), (16384, b"\x80\x80\x01")]:
            self.assertEqual(uvb(v), enc, f"{v}")
            self.assertEqual(bbl._Stream(enc).uvb(), v, f"{v}")

    def test_svb_zigzag(self):
        for v in (0, -1, 1, -2, 2, 63, -64, 1000, -1000, 2 ** 20, -(2 ** 20)):
            self.assertEqual(bbl._Stream(svb(v)).svb(), v, f"{v}")

    def test_varint_too_long_desyncs(self):
        with self.assertRaises(bbl._Desync):
            bbl._Stream(b"\xff\xff\xff\xff\xff\xff").uvb()

    def test_sign_extend(self):
        self.assertEqual(bbl._sign_extend(0b11, 2), -1)
        self.assertEqual(bbl._sign_extend(0b01, 2), 1)
        self.assertEqual(bbl._sign_extend(0x0F, 4), -1)
        self.assertEqual(bbl._sign_extend(0x7F, 8), 127)
        self.assertEqual(bbl._sign_extend(0x80, 8), -128)

    def test_c_div2_truncates_toward_zero(self):
        self.assertEqual(bbl._c_div2(5), 2)
        self.assertEqual(bbl._c_div2(-5), -2)   # Python의 -5//2 == -3 과 다르다
        self.assertEqual(bbl._c_div2(-4), -2)

    def test_tag2_3s32_all_selectors(self):
        cases = [
            [0, 1, -1],                 # 2비트
            [-8, 7, 0],                 # 4비트
            [-32, 31, 5],               # 6비트
            [-128, 127, 100],           # 8비트
            [-30000, 300, 0],           # 16비트
            [-8000000, 5, 70000],       # 24비트 혼합
            [2 ** 30, -(2 ** 30), 1],   # 32비트
        ]
        for vals in cases:
            enc = enc_tag2_3s32(vals)
            s = bbl._Stream(enc)
            self.assertEqual(bbl._tag2_3s32(s), vals, f"{vals} -> {enc.hex()}")
            self.assertEqual(s.pos, len(enc), f"{vals}: 소비 길이 불일치")

    def test_tag8_4s16_nibble_straddling(self):
        cases = [
            [0, 0, 0, 0],
            [1, -1, 2, -2],            # 4비트 네 개 = 니블 2바이트
            [7, -8, 0, 0],
            [100, -100, 1, 0],         # 8비트와 4비트 섞임
            [1, 200, 0, -3],           # 4비트 뒤 8비트 (니블 걸침)
            [1, 30000, 0, 0],          # 4비트 뒤 16비트 (니블 걸침)
            [-30000, 1, 20000, -7],
            [5, 6, 7000, -8000],
        ]
        for vals in cases:
            enc = enc_tag8_4s16(vals)
            s = bbl._Stream(enc)
            got = bbl._tag8_4s16(s)
            self.assertEqual(got, vals, f"{vals} -> {enc.hex()} -> {got}")
            self.assertEqual(s.pos, len(enc), f"{vals}: 소비 길이 불일치")

    def test_tag8_8svb(self):
        for vals in ([5], [0, 0, 0, 0], [1, 0, -1, 0], [100, -100, 0, 3],
                     [1, 2, 3, 4, 5, 6, 7, 8]):
            enc = enc_tag8_8svb(vals)
            s = bbl._Stream(enc)
            self.assertEqual(bbl._tag8_8svb(s, len(vals)), vals, f"{vals}")
            self.assertEqual(s.pos, len(enc), f"{vals}: 소비 길이 불일치")


# ---------------------------------------------------------------- 헤더/프레임


class HeaderTests(unittest.TestCase):
    def test_parses_field_definitions(self):
        data = make_log(synth_rows(64, 1000.0, {}))
        h, pos = bbl.parse_headers(data)
        self.assertEqual(h.main.names, FIELDS)
        self.assertEqual(h.main.i_enc, I_ENC)
        self.assertEqual(h.main.p_pred, P_PRED)
        self.assertEqual(h.slow.names, ["flightModeFlags", "stateFlags", "failsafePhase"])
        self.assertEqual(h.craft, "TESTBENCH")
        self.assertIn("INAV", h.firmware)
        self.assertEqual(data[pos:pos + 1], b"I")

    def test_rejects_file_without_headers(self):
        with self.assertRaises(bbl.BblError):
            bbl.parse_headers(b"not a blackbox log at all")

    def test_split_multiple_sessions(self):
        a = make_log(synth_rows(40, 1000.0, {}))
        b = make_log(synth_rows(40, 1000.0, {}))
        self.assertEqual(len(bbl.split_logs(a + b)), 2)


class DecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fs = 1000.0
        cls.rows = synth_rows(600, cls.fs, {0: [(120.0, 40.0)], 1: [(3.0, 25.0)], 2: []})
        cls.data = make_log(cls.rows)
        cls.d = bbl.decode(cls.data, bbl.split_logs(cls.data)[0])

    def test_round_trip_every_value(self):
        self.assertEqual(len(self.d.rows), len(self.rows))
        self.assertEqual(self.d.names, FIELDS)
        for n, (got, want) in enumerate(zip(self.d.rows, self.rows)):
            self.assertEqual(got, want, f"{n}번째 프레임 불일치")

    def test_no_resyncs_on_clean_log(self):
        self.assertEqual(self.d.resyncs, 0)
        self.assertFalse(self.d.truncated)

    def test_frame_counts_include_both_kinds(self):
        self.assertGreater(self.d.frame_counts.get("I", 0), 1)
        self.assertGreater(self.d.frame_counts.get("P", 0), 1)
        self.assertGreater(self.d.frame_counts.get("S", 0), 0)

    def test_structural_invariants(self):
        """실제 파일에서 디코더가 어긋나면 가장 먼저 깨지는 성질들."""
        it = self.d.col("loopIteration")
        self.assertEqual(it, list(range(len(it))))
        t = self.d.col("time")
        self.assertTrue(all(t[i + 1] > t[i] for i in range(len(t) - 1)))
        for i in range(4):
            m = self.d.col(f"motor[{i}]")
            self.assertTrue(all(1000 <= v <= 2000 for v in m), f"motor[{i}] 범위 이탈")

    def test_duration_matches_sample_count(self):
        self.assertAlmostEqual(self.d.duration_s, (len(self.rows) - 1) / self.fs, places=2)

    def test_resyncs_after_corruption_instead_of_dying(self):
        buf = bytearray(self.data)
        mid = len(buf) // 2
        for i in range(mid, mid + 24):
            buf[i] = 0xAB
        d = bbl.decode(bytes(buf), bbl.split_logs(bytes(buf))[0])
        self.assertGreater(d.resyncs, 0)
        # 손상 구간 이후에도 상당량을 복구해야 한다
        self.assertGreater(len(d.rows), len(self.rows) * 0.5)

    def test_max_seconds_limits_work(self):
        d = bbl.decode(self.data, bbl.split_logs(self.data)[0], max_seconds=0.1)
        self.assertTrue(d.truncated)
        self.assertLess(len(d.rows), len(self.rows))
        self.assertLessEqual(d.duration_s, 0.2)

    def test_unknown_encoding_is_reported(self):
        data = self.data.replace(b"H Field P encoding:9,0", b"H Field P encoding:9,5")
        with self.assertRaises(bbl.BblError):
            bbl.decode(data, bbl.split_logs(data)[0])

    def test_sessions_listing(self, ):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "LOG00001.BBL"
            p.write_bytes(self.data + make_log(synth_rows(40, 1000.0, {})))
            s = bbl.sessions(p)
            self.assertEqual(len(s), 2)
            self.assertEqual(s[0]["craft"], "TESTBENCH")
            d = bbl.decode_file(p, session=1)
            self.assertEqual(len(d.rows), 40)


# ---------------------------------------------------------------- 스펙트럼


class SpectrumTests(unittest.TestCase):
    def test_fft_matches_naive_dft(self):
        import cmath

        x = [math.sin(i * 0.7) + 0.3 * math.cos(i * 2.1) for i in range(32)]
        fast = bbl and None  # noqa
        got = __import__("d4d_fc.logan", fromlist=["_fft"])._fft([complex(v, 0) for v in x])
        n = len(x)
        for k in (0, 1, 5, 16, 31):
            want = sum(x[t] * cmath.exp(-2j * cmath.pi * k * t / n) for t in range(n))
            self.assertAlmostEqual(abs(got[k] - want), 0.0, places=8, msg=f"k={k}")

    def test_spectrum_finds_known_frequency(self):
        fs, f0, amp = 1000.0, 137.0, 20.0
        x = [amp * math.sin(2 * math.pi * f0 * i / fs) for i in range(4096)]
        freqs, power = spectrum(x, fs, 1024)
        peak = max(range(len(power)), key=lambda k: power[k])
        self.assertLess(abs(freqs[peak] - f0), fs / 1024 * 1.5)

    def test_band_rms_recovers_sine_amplitude(self):
        """정현파의 RMS는 진폭/sqrt(2). 파워 정규화가 맞는지 확인한다."""
        fs, f0, amp = 1000.0, 150.0, 30.0
        x = [amp * math.sin(2 * math.pi * f0 * i / fs) for i in range(8192)]
        freqs, power = spectrum(x, fs, 1024)
        rms = band_rms(freqs, power, 100.0, 200.0)
        self.assertAlmostEqual(rms, amp / math.sqrt(2), delta=amp * 0.08)

    def test_band_rms_excludes_other_bands(self):
        fs = 1000.0
        x = [10.0 * math.sin(2 * math.pi * 150.0 * i / fs) for i in range(8192)]
        freqs, power = spectrum(x, fs, 1024)
        self.assertLess(band_rms(freqs, power, 1.0, 5.0), 1.0)

    def test_rejects_non_power_of_two(self):
        from d4d_fc.logan import _fft

        with self.assertRaises(ValueError):
            _fft([1 + 0j] * 7)


class AnalysisTests(unittest.TestCase):
    def _analyze(self, freqs_amps, n=4096, fs=1000.0, throttle=1400):
        rows = synth_rows(n, fs, freqs_amps, throttle)
        data = make_log(rows)
        d = bbl.decode(data, bbl.split_logs(data)[0])
        self.assertEqual(d.resyncs, 0)
        return d, analyze(d)

    def test_detects_sample_rate_and_unit(self):
        d, a = self._analyze({})
        self.assertAlmostEqual(a.sample_rate_hz, 1000.0, delta=1.0)
        self.assertEqual(a.gyro_unit, "deg/s")  # 헤더가 INAV

    def test_classifies_high_frequency_noise(self):
        d, a = self._analyze({0: [(180.0, 45.0)]})
        self.assertEqual(a.symptom, "highfreq")
        roll = next(s for s in a.axes if s.axis == "roll")
        self.assertLess(abs(roll.top_peaks[0][0] - 180.0), 5.0)
        self.assertGreater(roll.bands["high"], 8.0)

    def test_classifies_low_frequency_wobble(self):
        d, a = self._analyze({1: [(3.0, 40.0)]})
        self.assertEqual(a.symptom, "wobble")

    def test_quiet_log_gets_no_symptom(self):
        d, a = self._analyze({0: [(2.0, 3.0)]})
        self.assertIsNone(a.symptom)
        self.assertTrue(any("기준값 아래" in f for f in a.findings))

    def test_high_frequency_wins_over_wobble(self):
        """둘 다 있으면 노이즈가 먼저다. 필터를 고치지 않으면 P/I 조정이 무의미하다."""
        d, a = self._analyze({0: [(170.0, 40.0)], 1: [(3.0, 40.0)]})
        self.assertEqual(a.symptom, "highfreq")

    def test_report_renders_and_links_to_tune(self):
        from d4d_fc.logan import render

        d, a = self._analyze({0: [(180.0, 45.0)]})
        md = "\n".join(render(d, a))
        self.assertIn("블랙박스 로그 분석", md)
        self.assertIn("TESTBENCH", md)
        self.assertIn("tune --symptom highfreq", md)
        self.assertIn("경험치", md)  # 기준값 한계를 반드시 밝힌다

    def test_motor_and_pid_stats_present(self):
        d, a = self._analyze({0: [(120.0, 30.0)]})
        self.assertIsNotNone(a.motor_mean)
        self.assertTrue(1000 <= a.motor_mean <= 2000)
        self.assertIn("P", a.pid_rms)
        self.assertAlmostEqual(a.throttle_mean, 1400.0, delta=1.0)

    def test_short_log_warns_instead_of_crashing(self):
        rows = synth_rows(100, 1000.0, {0: [(150.0, 20.0)]})
        data = make_log(rows)
        d = bbl.decode(data, bbl.split_logs(data)[0])
        a = analyze(d, nfft=1024)
        self.assertTrue(any("표본" in w for w in a.warnings))

    def test_bench_log_is_not_diagnosed(self):
        """모터가 공회전인 로그는 손으로 흔든 것이다. 진동 문제로 오진하면
        있지도 않은 PID 문제를 고치게 된다."""
        rows = synth_rows(4096, 1000.0, {0: [(3.0, 40.0)]}, throttle=MINTHROTTLE)
        for r in rows:  # 모터를 전부 공회전으로
            for k in range(4):
                r[FIELDS.index(f"motor[{k}]")] = MINTHROTTLE
        data = make_log(rows)
        d = bbl.decode(data, bbl.split_logs(data)[0])
        a = analyze(d)
        self.assertFalse(a.airborne)
        self.assertIsNone(a.symptom)
        self.assertTrue(any("날지 않은" in w for w in a.warnings))

    def test_flying_log_is_diagnosed(self):
        rows = synth_rows(4096, 1000.0, {1: [(3.0, 40.0)]}, throttle=1500)
        data = make_log(rows)
        d = bbl.decode(data, bbl.split_logs(data)[0])
        a = analyze(d)
        self.assertTrue(a.airborne)
        self.assertEqual(a.symptom, "wobble")

    def test_segments_split_on_time_gap(self):
        rows = synth_rows(3000, 1000.0, {0: [(150.0, 20.0)]})
        ti = FIELDS.index("time")
        for r in rows[1500:]:  # 중간에 60초 공백을 만든다
            r[ti] += 60_000_000
        data = make_log(rows)
        d = bbl.decode(data, bbl.split_logs(data)[0])
        segs = d.segments()
        self.assertGreaterEqual(len(segs), 2)
        self.assertLess(d.longest_segment()[1] - d.longest_segment()[0], len(rows))

    def test_p_interval_skipped_frames(self):
        """P interval 1/2 면 loopIteration 은 프레임마다 2씩 늘어야 한다."""
        self.assertEqual(bbl._p_interval("1/2"), (1, 2))
        self.assertEqual(bbl._p_interval("1/1"), (1, 1))
        self.assertEqual(bbl._p_interval("쓰레기"), (1, 1))

    def test_betaflight_units_suppress_absolute_thresholds(self):
        rows = synth_rows(4096, 1000.0, {0: [(180.0, 45.0)]})
        data = make_log(rows).replace(b"INAV 9.1.0", b"BTFL 4.5.0")
        d = bbl.decode(data, bbl.split_logs(data)[0])
        a = analyze(d)
        self.assertNotEqual(a.gyro_unit, "deg/s")
        self.assertTrue(any("절대 기준값" in w for w in a.warnings))


if __name__ == "__main__":
    unittest.main()

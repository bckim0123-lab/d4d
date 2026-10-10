#!/usr/bin/env python3
"""adpcm_host_test 출력을 voice_tool.py 해독기로 풀어 원본과 비교 (SNR)."""
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from voice_tool import adpcm_decode
orig = [int(x) for x in sys.stdin.readline().split(",")]
data = bytes.fromhex(sys.stdin.readline().strip())
dec = adpcm_decode(data, len(orig))
assert len(dec) == len(orig), (len(dec), len(orig))
sig = sum(x * x for x in orig)
err = sum((a - b) ** 2 for a, b in zip(orig, dec))
snr = 10 * math.log10(sig / err)
print(f"ADPCM 왕복: {len(orig)}샘플 → {len(data)}바이트 (압축 {len(orig) * 2 / len(data):.1f}:1), SNR {snr:.1f}dB")
assert snr > 20, "SNR이 너무 낮음"
print("통과")

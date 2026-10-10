#!/usr/bin/env python3
"""msp_host_test 가 출력한 SET_RAW_RC 프레임을 MSP v1 규격대로 독립 해석해 확인한다."""
import struct
import sys

frame = bytes.fromhex(sys.stdin.readline().strip())
assert frame[:3] == b"$M<", "헤더는 '$M<' 여야 함"
size, cmd = frame[3], frame[4]
payload, ck = frame[5:5 + size], frame[5 + size]
calc = size ^ cmd
for b in payload:
    calc ^= b
assert len(frame) == 6 + size, "길이 불일치"
assert cmd == 200, f"MSP_SET_RAW_RC(200) 이어야 함: {cmd}"
assert ck == calc, f"체크섬 불일치 {ck:#x} != {calc:#x}"
ch = struct.unpack("<" + "H" * (size // 2), payload)
assert ch == (1520, 1468, 1500, 1500, 1000, 2000, 1500, 1234), ch
print(f"Python 규격 검증 통과: cmd={cmd} size={size} checksum={ck:#04x} 채널={list(ch)}")

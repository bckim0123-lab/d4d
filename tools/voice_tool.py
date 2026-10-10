#!/usr/bin/env python3
"""태그인식 보드 마이크(INMP441) 도구 — USB 또는 블루투스.

  python3 tools/voice_tool.py level                      # 소리 크기 실시간 (박수·말소리로 확인)
  python3 tools/voice_tool.py rec --sec 2 --out a.wav    # 보드에서 녹음해 WAV로 저장
  python3 tools/voice_tool.py collect --keyword stop --count 20   # 학습 데이터 모으기 → dataset/stop/*.wav
  python3 tools/voice_tool.py cmd stop                   # 음성 명령 키워드 실행 (인식기 자리 시험)
  블루투스: uv run --with bleak --with pyserial python3 tools/voice_tool.py --ble level

음성 명령은 보드의 config.h VOICE_COMMANDS 표에 있는 키워드만 실행된다(정지·상태 등 안전한 것만).
인식기(PC 한국어 인식 또는 보드 키워드 모델)는 알아들은 결과를 `voicecmd <키워드>` 로 보내면 된다.
"""
import argparse
import base64
import json
import os
import struct
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tag_monitor import BleLink, find_port, open_port, read_lines, wait_boot  # noqa: E402

# ---------------------------------------------------------------------------
# IMA ADPCM 해독 (보드 src/adpcm.h 와 짝)
# ---------------------------------------------------------------------------
STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
        107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
        4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
        22385, 24623, 27086, 29794, 32767]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def adpcm_decode(data, n):
    pred, index, out = 0, 0, []
    for b in data:
        for code in (b & 0x0F, b >> 4):
            if len(out) >= n:
                return out
            step = STEP[index]
            diff = step >> 3
            if code & 4:
                diff += step
            if code & 2:
                diff += step >> 1
            if code & 1:
                diff += step >> 2
            pred = pred - diff if code & 8 else pred + diff
            pred = max(-32768, min(32767, pred))
            index = max(0, min(88, index + INDEX[code]))
            out.append(pred)
    return out


def save_wav(path, samples, rate):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))


# ---------------------------------------------------------------------------
# 보드와 주고받기
# ---------------------------------------------------------------------------
def connect(args):
    if args.ble:
        link = BleLink()
        return link, link.lines()
    s = open_port(args.port or find_port())
    lines = read_lines(s)
    wait_boot(lines)
    return s, lines


def record(link, lines, sec, timeout=20.0):
    """`rec <sec>` → MIC_BEGIN <rate> <samples> <adpcm_bytes> <b64_len> … MIC_END → int16 리스트"""
    link.write(f"rec {sec}\n".encode())
    t0, header, data = time.time(), None, []
    while time.time() - t0 < timeout:
        line = next(lines)
        if line is None:
            continue
        if line.startswith('{"type":"cmd_error"'):
            sys.exit("보드가 거부: " + json.loads(line)["reason"])
        if header is None:
            if line.startswith("MIC_BEGIN"):
                header = line.split()
            continue
        if line == "MIC_END":
            rate, n, nbytes, b64len = (int(x) for x in header[1:5])
            b64 = "".join(data)
            if len(b64) != b64len:
                print(f"경고: 받은 길이 {len(b64)} ≠ 예상 {b64len} (일부 누락)")
            raw = base64.b64decode(b64)
            return adpcm_decode(raw[:nbytes], n), rate
        if not line.startswith("{"):  # 녹음 줄 사이에 상태 JSON이 끼어들 수 있다
            data.append(line)
    sys.exit("녹음을 받지 못했습니다.")


def describe(samples, rate):
    if not samples:
        return "빈 녹음"
    peak = max(abs(x) for x in samples) / 32768
    rms = (sum(x * x for x in samples) / len(samples)) ** 0.5 / 32768
    import math
    db = 20 * math.log10(rms) if rms > 0 else -120
    return f"{len(samples) / rate:.2f}초, 최대 {peak * 100:.1f}%, 평균 {db:.1f}dBFS"


def cmd_level(link, lines, duration):
    print("소리 크기 (1초마다). 박수를 치거나 말해 보세요. Ctrl+C로 종료")
    t_end = time.time() + duration if duration else None
    for line in lines:
        if t_end and time.time() > t_end:
            return
        if not line or not line.startswith('{"type":"mic"'):
            continue
        o = json.loads(line)
        if not o.get("ok"):
            print(f"[마이크] 신호 없음 — 배선(SCK=D3, WS=D4, SD=D5, L/R=GND, 3.3V) 확인 · {o}")
            continue
        bar = "#" * max(0, int((o["rms_db"] + 70) / 2))
        print(f"[마이크] 평균 {o['rms_db']:6.1f}dBFS  최대 {o['peak_db']:6.1f}dBFS  {bar}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--ble", action="store_true", help="블루투스로 연결")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("level", help="소리 크기 실시간 보기")
    p.add_argument("--duration", type=float, default=0)
    p = sub.add_parser("rec", help="녹음해 WAV로 저장")
    p.add_argument("--sec", type=float, default=2.0)
    p.add_argument("--out", default="mic.wav")
    p = sub.add_parser("collect", help="키워드 학습 데이터 모으기")
    p.add_argument("--keyword", required=True, help="예: stop, resume, status")
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--sec", type=float, default=1.5)
    p.add_argument("--dir", default="dataset")
    p = sub.add_parser("cmd", help="음성 명령 키워드 실행")
    p.add_argument("keyword")
    args = ap.parse_args()

    link, lines = connect(args)
    try:
        if args.cmd == "level":
            cmd_level(link, lines, args.duration)
        elif args.cmd == "rec":
            samples, rate = record(link, lines, args.sec)
            save_wav(args.out, samples, rate)
            print(f"저장: {args.out} ({describe(samples, rate)})")
        elif args.cmd == "collect":
            folder = os.path.join(args.dir, args.keyword)
            for i in range(args.count):
                input(f"[{i + 1}/{args.count}] Enter 후 바로 '{args.keyword}' 를 말하세요 ({args.sec}초 녹음)… ")
                samples, rate = record(link, lines, args.sec)
                path = os.path.join(folder, f"{args.keyword}_{time.strftime('%Y%m%d_%H%M%S')}_{i:03d}.wav")
                save_wav(path, samples, rate)
                print(f"  저장 {path} ({describe(samples, rate)})")
        elif args.cmd == "cmd":
            link.write(f"voicecmd {args.keyword}\n".encode())
            t0 = time.time()
            for line in lines:
                if time.time() - t0 > 5:
                    print("응답 없음")
                    break
                if line and (line.startswith('{"type":"voice"') or line.startswith('{"type":"cmd_error"')):
                    print(line)
                    break
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    main()

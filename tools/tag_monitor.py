#!/usr/bin/env python3
"""태그인식 보드(XIAO ESP32S3 Sense) 모니터 — USB 시리얼 또는 블루투스(BLE).

펌웨어가 내보내는 JSON 줄을 한글로 풀어 보여준다.

  python3 tools/tag_monitor.py                 # 실시간 해석 (USB)
  uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble   # 블루투스로 연결
  python3 tools/tag_monitor.py --snap out.jpg  # 카메라 화면 한 장 저장
  python3 tools/tag_monitor.py --cmd "size 0 100"       # ID 0 태그 크기(mm)
  python3 tools/tag_monitor.py --cmd "land 10 -170 0"   # ID 10 중심 → 착륙 지점 오프셋(mm)
  python3 tools/tag_monitor.py --cmd "dec auto"         # decimate 자동 (기본)
  python3 tools/tag_monitor.py --cmd "msp off"          # FC로 MSP 송신 중지 (on 으로 재개)
  python3 tools/tag_monitor.py --cmd "mission reset"    # 미션 단계를 '정렬'로 되돌림
  python3 tools/tag_monitor.py --raw           # JSON 원문 그대로

주의: USB 포트를 열면 보드가 재부팅될 수 있다(ESP32-S3 내장 USB 특성). 블루투스는 재부팅하지 않는다.
블루투스는 BLE Nordic UART Service(이름 TagBoard-XXXX)이며, 다른 옵션은 USB와 똑같이 쓴다.
"""
import argparse
import asyncio
import base64
import glob
import json
import queue
import sys
import threading
import time

import serial


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*")) + sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        sys.exit("보드 포트를 찾지 못했습니다. USB-C 연결을 확인하세요.")
    return ports[0]


def open_port(port):
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.2
    s.dtr = False
    s.rts = False
    s.open()
    return s


def read_lines(s):
    buf = b""
    while True:
        chunk = s.read(4096)
        if not chunk:
            yield None
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", "replace").strip()


class BleLink:
    """BLE Nordic UART Service 연결. USB 쪽과 같은 write()/lines()를 제공한다."""

    TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # 보드 → PC (알림)
    RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # PC → 보드 (명령)

    def __init__(self, name_prefix="TagBoard", timeout=15.0):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            sys.exit("블루투스 연결에는 bleak가 필요합니다:\n"
                     "  uv run --with bleak --with pyserial python3 tools/tag_monitor.py --ble")
        self.q = queue.Queue()
        self.buf = bytearray()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        async def connect():
            dev = await BleakScanner.find_device_by_filter(
                lambda d, ad: (d.name or ad.local_name or "").startswith(name_prefix), timeout=timeout)
            if dev is None:
                return None
            client = BleakClient(dev, disconnected_callback=lambda _: print("[블루투스] 연결 끊김", flush=True))
            await client.connect()
            await client.start_notify(self.TX, self._on_data)
            return client, dev

        found = asyncio.run_coroutine_threadsafe(connect(), self.loop).result(timeout + 20)
        if not found:
            sys.exit("블루투스로 보드를 찾지 못했습니다 (이름 TagBoard-…). 보드 전원과 거리를 확인하세요.")
        self.client, dev = found
        print(f"[블루투스] {dev.name} 연결됨 (MTU {self.client.mtu_size})", flush=True)

    def _on_data(self, _, data):
        self.buf.extend(data)
        while b"\n" in self.buf:
            i = self.buf.index(b"\n")
            self.q.put(bytes(self.buf[:i]).decode("utf-8", "replace").strip())
            del self.buf[:i + 1]

    def write(self, data):
        asyncio.run_coroutine_threadsafe(self.client.write_gatt_char(self.RX, data, response=False), self.loop).result(5)

    def lines(self):
        while True:
            try:
                yield self.q.get(timeout=0.2)
            except queue.Empty:
                yield None

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)


def wait_boot(lines, timeout=8.0):
    """재부팅 직후 명령이 무시되지 않도록 boot 메시지(또는 아무 JSON)를 기다린다."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = next(lines)
        if line and line.startswith("{"):
            return
    # 재부팅하지 않은 경우에도 계속 진행


def fmt_tag(o):
    reg = "" if o.get("registered") else " · 미등록"
    land = f" · 착륙점 오프셋 ({o['land_x_mm']},{o['land_y_mm']})mm" if (o["land_x_mm"] or o["land_y_mm"]) else ""
    return (
        f"  └ 태그 ID {o['id']} ({o['meaning']}{reg}) · 한 변 {o['tag_size_mm']}mm → {o['side_px']:.0f}px"
        f" · 회전 {o['rot_deg']:+.0f}° · 추정거리 {o['dist_est_cm']:.0f}cm · margin {o['margin']:.0f}{land}"
    )


def fmt_target(o):
    h = f"{o['height_cm']:.0f}cm" if o.get("height_cm") is not None else "없음"
    axes = "" if o.get("axes_verified") else " (축 방향 미검증)"
    many = f" · 후보 {o['candidates']}개 중 가장 크게 보이는 태그" if o["candidates"] > 1 else ""
    return (
        f"[판단] ID {o['id']} 기준{many} · 단계 {PHASE_KO.get(o.get('phase'), o.get('phase'))} · decimate {o['decimate']}\n"
        f"       {'착륙 지점 바로 위' if o['decision'] == 'OVER_TARGET' else '이동 필요'}"
        f" | 이동: {o['move_ko']}{axes}\n"
        f"       다음: {o['next_ko']}\n"
        f"       착륙 지점: 화면 중심 대비 오른쪽 {o['img_right_cm']:+.1f}cm · 위쪽 {o['img_up_cm']:+.1f}cm"
        f" | 추정거리 {o['dist_est_cm']:.0f}cm | MTF 높이 {h}"
    )


def fmt_flow(o):
    if not o.get("connected"):
        return "[MTF-01P] 연결 안 됨 (데이터 없음)"
    return (
        f"[MTF-01P] 높이 {o['height_cm']:.1f}cm | 앞 {o['forward_cm_s']:+.1f} · 오른쪽 {o['right_cm_s']:+.1f}"
        f" · 위 {o['up_cm_s']:+.1f} cm/s | 품질 {o['quality']} | 패킷 {o['packets']} 오류 {o['errors']}"
    )


def fmt_vision(o):
    if not o.get("camera"):
        return "[카메라] 초기화 실패"
    seen = f"ID {o['ids']} (미션 태그 아님)" if o["tags"] else "태그 안 보임"
    return (
        f"[카메라] {o['fps']:.1f}fps · 검출 {o['detect_ms']}ms · {seen}"
        f" · decimate {o['decimate_used']}→{o['decimate_next']} ({o['dec_mode']}: {DEC_REASON.get(o['dec_reason'], o['dec_reason'])})"
    )


PHASE_KO = {
    "ALIGN": "정렬",
    "GOTO_50": "50cm로 이동",
    "HOLD_50": "50cm 유지",
    "GOTO_100": "100cm로 이동",
    "HOVER": "호버링(완료)",
}


def fmt_msp(o):
    if not o["enabled"]:
        return "[MSP→FC] 송신 꺼짐 (msp on 으로 켜기) — INAV는 조종기 입력 사용"
    names = ["롤", "피치", "스로틀", "요"]
    out = " ".join(f"{n}={v}" for n, v in zip(names, o["out"]))
    link = "FC 응답 OK" if o["fc_link"] else "FC 응답 없음(배선·INAV Ports MSP 확인)"
    fc = ""
    if o["fc_rc"]:
        # MSP_RC 응답은 INAV 내부 순서(롤·피치·요·스로틀)다. 보내는 값(AETR)과 3·4번 자리가 다르다.
        fc_names = ["롤", "피치", "요", "스로틀", "AUX1", "AUX2", "AUX3", "AUX4"]
        fc = " | FC가 쓰는 값: " + " ".join(f"{n}={v}" for n, v in zip(fc_names, o["fc_rc"]))
    thr = "" if o["throttle_ctrl"] else " (스로틀 제어 꺼짐)"
    return (f"[MSP→FC] {link} · 보냄 {o['sent']} 응답 {o['acks']} 오류 {o['errors']} | 송신: {out}{thr}{fc}"
            f" | 단계 {PHASE_KO.get(o['phase'], o['phase'])}")


DEC_REASON = {
    "tag_px": "보이는 태그 크기 기준",
    "mtf_height": "MTF 높이로 태그 크기 예측",
    "sweep": "주기적 넓게 찾기",
    "search": "높이 정보 없음 → 넓게 찾기",
    "manual": "수동 고정",
    "start": "시작값",
}


def fmt_boot(o):
    return (
        f"[부팅] {o['build']} | 카메라 {o['camera_sensor'] if o['camera'] else '실패'} {o['frame']}"
        f" | {o['tag_family']} 등록 태그 {o['tags_registered']}개 · decimate {o['decimate']} ({o['dec_mode']})"
        f" | PSRAM 여유 {o['psram_free'] // 1024}KB | MTF RX{o['rx']}/TX{o['tx']}"
        f" | FC MSP {'켜짐' if o.get('msp') else '꺼짐'} (GPIO{o.get('fc_tx')}→RX5, GPIO{o.get('fc_rx')}←TX5)"
        f" | BLE {o.get('ble', '없음')}{' 연결 중' if o.get('ble_connected') else ''}"
    )


def monitor(lines, raw, flow_every, deadline):
    last_flow = last_msp = last_mic = 0.0
    for line in lines:
        if deadline and time.time() > deadline:
            return
        if not line:
            continue
        if raw:
            print(line, flush=True)
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue  # ROM 부트 로그 등
        t = o.get("type")
        if t == "tag":
            print(fmt_tag(o), flush=True)
        elif t == "target":
            print(fmt_target(o), flush=True)
        elif t == "tags":
            reg = ", ".join(f"ID {x['id']}={x['size_mm']}mm 착륙점({x['land_x_mm']},{x['land_y_mm']})" for x in o["tags"])
            print(f"[태그 표] {reg}", flush=True)
        elif t == "mic":
            if time.time() - last_mic >= flow_every:
                last_mic = time.time()
                print(f"[마이크] 평균 {o['rms_db']:.1f}dBFS · 최대 {o['peak_db']:.1f}dBFS" if o.get("ok")
                      else "[마이크] 신호 없음 (배선 확인)", flush=True)
        elif t == "voice":
            print(f"[음성 명령] {o['keyword']} ({o['ko']}) → {o['commands']}", flush=True)
        elif t == "msp":
            if time.time() - last_msp >= flow_every:
                last_msp = time.time()
                print(fmt_msp(o), flush=True)
        elif t == "flow":
            if time.time() - last_flow >= flow_every:
                last_flow = time.time()
                print(fmt_flow(o), flush=True)
        elif t == "vision":
            if o.get("target_id", -1) < 0:
                print(fmt_vision(o), flush=True)
        elif t in ("boot", "info"):
            print(fmt_boot(o), flush=True)
        else:
            print(line, flush=True)


def snap(s, lines, path, timeout=15.0):
    s.write(b"snap\n")
    t0 = time.time()
    header = None
    data = []
    while time.time() - t0 < timeout:
        line = next(lines)
        if line is None:
            continue
        if header is None:
            if line.startswith("SNAP_BEGIN"):
                header = line.split()
            continue
        if line == "SNAP_END":
            b64 = "".join(data)
            if len(b64) != int(header[4]):
                print(f"경고: base64 길이 {len(b64)} ≠ 예상 {header[4]} (일부 줄 누락)")
            jpg = base64.b64decode(b64)
            open(path, "wb").write(jpg)
            print(f"저장: {path} ({header[2]}x{header[3]}, {len(jpg)} bytes)")
            return True
        if not line.startswith("{"):  # 스냅샷 줄 사이에 다른 JSON 줄이 끼어들 수 있다
            data.append(line)
    sys.exit("스냅샷을 받지 못했습니다.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--ble", action="store_true", help="USB 대신 블루투스(BLE)로 연결")
    ap.add_argument("--snap", metavar="JPG", help="카메라 화면 한 장을 저장하고 종료")
    ap.add_argument("--cmd", help="보드에 명령 한 줄 보내기 (예: 'tags', 'size 0 100', 'land 10 -170 0', 'dec auto', 'dec 3')")
    ap.add_argument("--raw", action="store_true", help="JSON 원문 출력")
    ap.add_argument("--flow-every", type=float, default=1.0, help="MTF-01P 줄 출력 간격(초)")
    ap.add_argument("--duration", type=float, default=0, help="이 시간(초) 후 종료 (0 = 계속)")
    args = ap.parse_args()

    if args.ble:
        s = BleLink()
        lines = s.lines()
        s.write(b"info\n")  # BLE는 재부팅이 없으니 현재 상태를 한 번 받아 둔다
    else:
        s = open_port(args.port or find_port())
        lines = read_lines(s)
        wait_boot(lines)  # USB는 포트를 열면 재부팅되므로 부팅을 기다린다

    if args.snap:
        snap(s, lines, args.snap)
        return
    if args.cmd:
        s.write((args.cmd + "\n").encode())

    deadline = time.time() + args.duration if args.duration else 0
    try:
        monitor(lines, args.raw, args.flow_every, deadline)
    except KeyboardInterrupt:
        pass
    finally:
        s.close()


if __name__ == "__main__":
    main()

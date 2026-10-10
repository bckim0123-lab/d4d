"""MSP(MultiWii Serial Protocol) v1/v2 클라이언트.

INAV 비행제어기와 USB VCP/UART로 통신한다. 이 모듈은 프레이밍과 요청/응답만
담당하며, 어떤 명령이 안전한지는 판단하지 않는다(codes.py의 WRITE_COMMANDS 참고).
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

try:
    import serial
except ImportError as e:  # pragma: no cover
    raise SystemExit("pyserial이 필요합니다: pip install pyserial") from e


class MspError(RuntimeError):
    pass


class MspTimeout(MspError):
    pass


class MspRejected(MspError):
    """FC가 '$M!' / 에러 플래그로 거부한 경우(미지원 명령, 잘못된 인자 등)."""


def _crc8_dvb_s2(crc: int, b: int) -> int:
    crc ^= b
    for _ in range(8):
        crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = _crc8_dvb_s2(crc, b)
    return crc


def encode_v1(cmd: int, payload: bytes = b"") -> bytes:
    if cmd > 254 or len(payload) > 255:
        raise ValueError("MSP v1 범위를 넘습니다. v2를 사용하세요.")
    body = bytes([len(payload), cmd]) + payload
    ck = 0
    for b in body:
        ck ^= b
    return b"$M<" + body + bytes([ck])


def encode_v2(cmd: int, payload: bytes = b"") -> bytes:
    body = b"\x00" + struct.pack("<HH", cmd, len(payload)) + payload
    return b"$X<" + body + bytes([_crc8(body)])


@dataclass
class Reply:
    cmd: int
    payload: bytes
    version: int


class MspClient:
    """읽기 중심의 MSP 클라이언트.

    with MspClient("COM9") as fc:
        print(fc.identity())
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.2, dtr: bool = True):
        self.port_name = port
        self.ser = serial.Serial(port, baudrate, timeout=0.05, write_timeout=1.0, dsrdtr=False)
        self.ser.dtr = dtr
        self.ser.rts = False
        self.timeout = timeout
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.last_rejected: list[int] = []

    # ---------------------------------------------------------------- 수명주기
    def close(self) -> None:
        if self.ser.is_open:
            self.ser.close()

    def __enter__(self) -> "MspClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 저수준
    def _read_exact(self, n: int, deadline: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            if time.monotonic() > deadline:
                raise MspTimeout(f"{n}바이트 대기 중 타임아웃(수신 {len(buf)})")
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
        return bytes(buf)

    def _read_reply(self, want: int, deadline: float) -> Reply:
        """원하는 cmd의 응답이 올 때까지 프레임을 읽는다(비동기 텔레메트리 섞임 대비)."""
        while True:
            if time.monotonic() > deadline:
                raise MspTimeout(f"cmd {want} 응답 타임아웃")
            b = self.ser.read(1)
            if not b:
                continue
            if b != b"$":
                continue
            ver = self._read_exact(1, deadline)
            direction = self._read_exact(1, deadline)
            if direction not in (b">", b"!"):
                continue
            if ver == b"M":
                head = self._read_exact(2, deadline)
                ln, cmd = head[0], head[1]
                payload = self._read_exact(ln, deadline) if ln else b""
                ck = self._read_exact(1, deadline)[0]
                calc = 0
                for x in head + payload:
                    calc ^= x
                if calc != ck:
                    continue
                v = 1
            elif ver == b"X":
                head = self._read_exact(5, deadline)
                _flag, cmd, ln = struct.unpack("<BHH", head)
                payload = self._read_exact(ln, deadline) if ln else b""
                crc = self._read_exact(1, deadline)[0]
                if _crc8(head + payload) != crc:
                    continue
                v = 2
            else:
                continue
            if direction == b"!":
                if cmd == want:
                    raise MspRejected(f"FC가 cmd {want}를 거부했습니다(미지원 또는 잘못된 인자).")
                continue
            if cmd != want:
                continue
            return Reply(cmd, payload, v)

    # ---------------------------------------------------------------- 고수준
    def request(self, cmd: int, payload: bytes = b"", retries: int = 2, timeout: float | None = None) -> bytes:
        """cmd를 요청하고 응답 payload를 돌려준다. cmd > 254면 자동으로 MSP v2를 쓴다."""
        frame = encode_v2(cmd, payload) if (cmd > 254 or len(payload) > 255) else encode_v1(cmd, payload)
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                self.ser.reset_input_buffer()
                self.ser.write(frame)
                self.ser.flush()
                return self._read_reply(cmd, time.monotonic() + (timeout or self.timeout)).payload
            except MspRejected:
                raise
            except MspError as e:
                last = e
        raise last if last else MspTimeout(f"cmd {cmd} 실패")

    def try_request(self, cmd: int, payload: bytes = b"", **kw) -> bytes | None:
        """미지원/타임아웃이면 None. 펌웨어 버전별 명령 차이를 흡수한다."""
        try:
            return self.request(cmd, payload, **kw)
        except MspError:
            self.last_rejected.append(cmd)
            return None

"""스냅샷 재생 클라이언트.

케이블을 뽑은 뒤에도 MspClient와 똑같이 동작한다. 하드웨어 없이 조사 도구,
디코더, 조정 UI를 개발하고 테스트할 수 있다.

    from d4d_fc.replay import ReplayClient
    with ReplayClient.from_file("out/fc/fc_snapshot.json") as fc:
        print(fc.request(2))   # b"INAV"
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from . import codes as C
from .msp import MspError, MspTimeout


class ReplayClient:
    """스냅샷을 실제 FC처럼 응답한다. 쓰기 명령은 거부한다."""

    def __init__(self, snap: dict[str, Any], loop_live: bool = True):
        self.snap = snap
        self.port_name = f"replay:{snap.get('port', '?')}"
        self.last_rejected: list[int] = []
        self.loop_live = loop_live
        self._cursor: dict[int, int] = {}

        self._by_cmd: dict[int, bytes] = {}
        for entry in snap.get("frames", {}).values():
            self._by_cmd[entry["cmd"]] = bytes.fromhex(entry["hex"])

        s = snap.get("settings", {})
        self._settings_raw = {int(k): bytes.fromhex(v) for k, v in s.get("raw_by_index", {}).items()}
        self._name_to_index = dict(s.get("name_to_index", {}))

        self._series: dict[int, list[bytes]] = {}
        data = snap.get("live_series", {}).get("data", {})
        name_to_cmd = {name: entry["cmd"] for name, entry in snap.get("frames", {}).items()}
        for name, samples in data.items():
            cmd = name_to_cmd.get(name)
            if cmd is None or not samples:
                continue
            self._series[cmd] = [bytes.fromhex(x["hex"]) for x in samples]

    # ---------------------------------------------------------------- 생성
    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "ReplayClient":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"스냅샷을 찾을 수 없습니다: {p}")
        snap = json.loads(p.read_text(encoding="utf-8"))
        if snap.get("schema") != "d4d.fc.snapshot/1":
            raise ValueError(f"스냅샷 형식이 아닙니다: {p}")
        return cls(snap, **kw)

    # ---------------------------------------------------------------- 수명주기
    def close(self) -> None:
        pass

    def __enter__(self) -> "ReplayClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 요청
    def request(self, cmd: int, payload: bytes = b"", retries: int = 0, timeout: float | None = None) -> bytes:
        if cmd in C.WRITE_COMMANDS:
            raise MspError(f"재생 모드에서는 쓰기 명령({cmd})을 처리하지 않습니다.")

        if cmd == C.MSP2_COMMON_SETTING_INFO:
            return self._setting_info(payload)

        if cmd in self._series:
            lst = self._series[cmd]
            i = self._cursor.get(cmd, 0)
            if i >= len(lst):
                if not self.loop_live:
                    raise MspTimeout(f"cmd {cmd} 시계열이 끝났습니다.")
                i = 0
            self._cursor[cmd] = i + 1
            return lst[i]

        if cmd in self._by_cmd:
            return self._by_cmd[cmd]
        raise MspTimeout(f"스냅샷에 cmd {cmd} 응답이 없습니다.")

    def try_request(self, cmd: int, payload: bytes = b"", **kw) -> bytes | None:
        try:
            return self.request(cmd, payload, **kw)
        except MspError:
            self.last_rejected.append(cmd)
            return None

    # ---------------------------------------------------------------- 설정 조회
    def _setting_info(self, payload: bytes) -> bytes:
        if payload[:1] == b"\x00" and len(payload) >= 3:
            idx = struct.unpack_from("<H", payload, 1)[0]
        else:
            name = payload.split(b"\x00")[0].decode("ascii", "replace")
            if name not in self._name_to_index:
                raise MspTimeout(f"스냅샷에 설정 '{name}'이 없습니다.")
            idx = self._name_to_index[name]
        if idx not in self._settings_raw:
            raise MspTimeout(f"스냅샷에 설정 index {idx}가 없습니다.")
        return self._settings_raw[idx]

    # ---------------------------------------------------------------- 편의
    def setting(self, name: str) -> dict:
        from . import decode as D

        return D.setting_info(self._setting_info(name.encode() + b"\x00"))

    @property
    def live_length(self) -> int:
        return max((len(v) for v in self._series.values()), default=0)

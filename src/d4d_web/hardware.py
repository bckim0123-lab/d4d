"""Local-only FC access and two-step volatile control authorization."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from d4d_fc.control import ALLOWED_OVERRIDE_MASKS, GROUND_CONTROL_MASKS
from d4d_fc.control_write import (
    CONFIRMATION,
    ControlWriteBlocked,
    apply_override_mask,
    read_control_state,
)
from d4d_fc.msp import MspClient, MspError


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "hardware" / "profile.json"
FC_ACCESS_LOCK = threading.Lock()
PRIVATE_PROFILE_KEYS = frozenset({"uid", "mac", "address"})


class HardwareError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _without_private_identifiers(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_private_identifiers(child)
            for key, child in value.items()
            if key.lower() not in PRIVATE_PROFILE_KEYS
        }
    if isinstance(value, list):
        return [_without_private_identifiers(child) for child in value]
    return value


class HardwareService:
    """Owns serialized, short COM sessions and in-memory challenge tokens."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        profile_path: str | Path = PROFILE_PATH,
        now: Callable[[], float] | None = None,
        token_ttl: float = 30.0,
        backup_dir: str | Path | None = None,
        access_lock: threading.Lock = FC_ACCESS_LOCK,
    ):
        self._client_factory = client_factory or self._default_client
        self._profile_path = Path(profile_path)
        self._now = now or time.monotonic
        self._token_ttl = token_ttl
        self._backup_dir = Path(backup_dir or (ROOT / "snapshots" / "control"))
        self._access_lock = access_lock
        self._token_lock = threading.Lock()
        self._tokens: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _default_client() -> MspClient:
        return MspClient(
            os.environ.get("D4D_FC_PORT", "COM9"),
            int(os.environ.get("D4D_FC_BAUD", "115200")),
        )

    @staticmethod
    def _writes_enabled() -> bool:
        return os.environ.get("D4D_HARDWARE_WRITES") == "1"

    @staticmethod
    def _validate_target(target: int) -> None:
        if type(target) is not int or target not in ALLOWED_OVERRIDE_MASKS:
            raise HardwareError(
                "invalid_target",
                "target은 7, 64, 67, 71 중 하나여야 합니다.",
                422,
            )

    @contextmanager
    def _client(self) -> Iterator[Any]:
        if not self._access_lock.acquire(timeout=0.25):
            raise HardwareError(
                "fc_busy",
                "다른 요청이 FC 직렬 포트를 사용 중입니다.",
                409,
            )
        client: Any | None = None
        try:
            try:
                client = self._client_factory()
            except (OSError, MspError) as exc:
                raise HardwareError(
                    "fc_unavailable",
                    f"FC 포트를 열 수 없습니다: {exc}",
                    503,
                ) from exc
            yield client
        finally:
            if client is not None:
                try:
                    client.close()
                except (AttributeError, OSError):
                    pass
            self._access_lock.release()

    def profile(self) -> dict[str, Any]:
        try:
            data = json.loads(self._profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HardwareError(
                "profile_unavailable",
                f"하드웨어 프로필을 읽을 수 없습니다: {exc}",
                503,
            ) from exc
        return _without_private_identifiers(data)

    def status(self) -> dict[str, Any]:
        try:
            with self._client() as fc:
                data, readiness = read_control_state(fc)
        except HardwareError:
            raise
        except (ControlWriteBlocked, MspError, OSError, ValueError) as exc:
            raise HardwareError(
                "fc_unavailable",
                f"FC 상태를 읽을 수 없습니다: {exc}",
                503,
            ) from exc

        inav = data["status"]["inav_status"]
        return {
            "port": os.environ.get("D4D_FC_PORT", "COM9"),
            "identity": data["identity"],
            "armed": inav.get("armed"),
            "arm_blockers": inav.get("arm_blockers", []),
            "sensors": inav.get("sensors", []),
            "rc": data["live"]["rc"],
            "motors_us": data["live"]["motor_outputs_us"],
            "modes": data["modes"]["configured_mode_switches"],
            "serial_ports": data["hardware"]["serial_ports"],
            "mask": readiness.current_mask,
            "min_check": readiness.min_check,
            "throttle_us": readiness.throttle_us,
            "can_apply_67": readiness.can_apply_67,
            "can_apply_override": readiness.can_apply_override,
            "target_masks": sorted(GROUND_CONTROL_MASKS),
            "allowed_masks": sorted(ALLOWED_OVERRIDE_MASKS),
            "can_restore_64": readiness.can_restore_64,
            "gates": readiness.to_dict()["gates"],
            "flight_locked": True,
        }

    def challenge(self, target: int, confirmation: str) -> dict[str, Any]:
        self._validate_target(target)
        if not self._writes_enabled():
            raise HardwareError(
                "writes_disabled",
                "D4D_HARDWARE_WRITES=1일 때만 RAM 변경을 허용합니다.",
                403,
            )
        if confirmation != CONFIRMATION:
            raise HardwareError(
                "confirmation_mismatch",
                f"확인 문구를 정확히 입력하세요: {CONFIRMATION}",
                400,
            )

        live = self.status()
        permitted = (
            live["can_apply_override"]
            if target in GROUND_CONTROL_MASKS
            else live["can_restore_64"]
        )
        if not permitted:
            failed = [
                gate["label"]
                for gate in live["gates"]
                if not gate["passed"]
                and (
                    gate["required_for_apply"]
                    if target in GROUND_CONTROL_MASKS
                    else gate["required_for_restore"]
                )
            ]
            raise HardwareError(
                "safety_gate",
                "안전 게이트가 닫혀 있습니다: " + ", ".join(failed),
                409,
            )

        token = secrets.token_urlsafe(24)
        expires_at = self._now() + self._token_ttl
        with self._token_lock:
            self._tokens[token] = {
                "target": target,
                "expires_at": expires_at,
                "confirmation": confirmation,
            }
        return {
            "token": token,
            "target": target,
            "expires_in_s": self._token_ttl,
            "mask": live["mask"],
            "gates": live["gates"],
        }

    def _consume_token(self, token: str, target: int) -> dict[str, Any]:
        with self._token_lock:
            record = self._tokens.pop(token, None)
        if record is None:
            raise HardwareError(
                "challenge_invalid",
                "일회성 확인 토큰이 없거나 이미 사용됐습니다.",
                409,
            )
        if self._now() > record["expires_at"]:
            raise HardwareError(
                "challenge_expired",
                "확인 토큰이 만료됐습니다. 현재 상태를 다시 확인하세요.",
                409,
            )
        if record["target"] != target:
            raise HardwareError(
                "challenge_target",
                "확인한 target과 적용할 target이 다릅니다.",
                409,
            )
        return record

    def apply(self, target: int, token: str) -> dict[str, Any]:
        self._validate_target(target)
        if not self._writes_enabled():
            raise HardwareError(
                "writes_disabled",
                "D4D_HARDWARE_WRITES=1일 때만 RAM 변경을 허용합니다.",
                403,
            )
        record = self._consume_token(token, target)

        try:
            with self._client() as fc:
                return apply_override_mask(
                    fc,
                    target,
                    confirmation=record["confirmation"],
                    writes_enabled=True,
                    backup_dir=self._backup_dir,
                )
        except HardwareError:
            raise
        except ControlWriteBlocked as exc:
            raise HardwareError("safety_gate", str(exc), 409) from exc
        except (MspError, OSError, ValueError) as exc:
            raise HardwareError(
                "fc_unavailable",
                f"FC RAM 변경 중 통신 오류가 발생했습니다: {exc}",
                503,
            ) from exc

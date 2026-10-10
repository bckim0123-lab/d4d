"""계획 산출물 라이브러리와 승인 패키지 관리."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def project_root() -> Path:
    """설치/소스 실행 모두에서 프로젝트 루트를 찾는다."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd().resolve()


ROOT = project_root()
ARTIFACTS_DIR = ROOT / "artifacts"
SCENARIOS_DIR = ROOT / "scenarios"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LibraryError(ValueError):
    pass


def safe_id(value: str, label: str = "id") -> str:
    if not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise LibraryError(f"유효하지 않은 {label}: {value!r}")
    return value


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scenario_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_dir(run_id: str) -> Path:
    return ARTIFACTS_DIR / safe_id(run_id, "run_id")


def metadata_path(run_id: str) -> Path:
    return run_dir(run_id) / "metadata.json"


def read_metadata(run_id: str) -> dict[str, Any]:
    path = metadata_path(run_id)
    if not path.is_file():
        raise LibraryError(f"실행을 찾을 수 없습니다: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_metadata(run_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    path = metadata_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata["updated_at"] = utc_now()
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    update_readme()
    return metadata


def create_run(run_id: str, scenario_id: str | None, scenario: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    out = run_dir(run_id)
    if out.exists():
        raise LibraryError(f"이미 존재하는 run_id입니다: {run_id}")
    out.mkdir(parents=True)
    (out / "scenario.json").write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    now = utc_now()
    meta: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_hash": scenario_hash(scenario),
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "success": None,
        "files": ["scenario.json"],
    }
    write_metadata(run_id, meta)
    return out, meta


def list_runs() -> list[dict[str, Any]]:
    if not ARTIFACTS_DIR.exists():
        return []
    records = []
    for path in ARTIFACTS_DIR.iterdir():
        if path.is_dir() and (path / "metadata.json").is_file():
            try:
                records.append(json.loads((path / "metadata.json").read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return sorted(records, key=lambda x: x.get("created_at", ""), reverse=True)


def refresh_status(run_id: str) -> dict[str, Any]:
    meta = read_metadata(run_id)
    sid = meta.get("scenario_id")
    if sid and meta.get("status") == "approved":
        source = SCENARIOS_DIR / f"{safe_id(sid, 'scenario_id')}.json"
        if not source.is_file():
            meta["status"] = "stale"
            meta["stale_reason"] = "시나리오가 삭제되었습니다."
            return write_metadata(run_id, meta)
        current = json.loads(source.read_text(encoding="utf-8"))
        if scenario_hash(current) != meta["scenario_hash"]:
            meta["status"] = "stale"
            meta["stale_reason"] = "승인 후 시나리오가 변경되었습니다."
            return write_metadata(run_id, meta)
    return meta


def approve_run(run_id: str, approved_by: str = "operator") -> dict[str, Any]:
    meta = refresh_status(run_id)
    if not meta.get("success"):
        raise LibraryError("검증에 통과한 계획만 승인할 수 있습니다.")
    scenario = json.loads((run_dir(run_id) / "scenario.json").read_text(encoding="utf-8"))
    sid = meta.get("scenario_id")
    if sid:
        source = SCENARIOS_DIR / f"{safe_id(sid, 'scenario_id')}.json"
        if not source.is_file() or scenario_hash(json.loads(source.read_text(encoding="utf-8"))) != scenario_hash(scenario):
            raise LibraryError("현재 시나리오와 계획의 해시가 달라 승인할 수 없습니다.")
    meta.update(status="approved", approved_at=utc_now(), approved_by=approved_by)
    meta.pop("stale_reason", None)
    return write_metadata(run_id, meta)


def package_run(run_id: str) -> Path:
    meta = refresh_status(run_id)
    if meta.get("status") != "approved":
        raise LibraryError("승인된 최신 계획만 패키지로 만들 수 있습니다.")
    source_dir = run_dir(run_id)
    package = source_dir / f"{run_id}.zip"
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=source_dir) as tmp:
        temp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(source_dir.rglob("*")):
                if path.is_file() and path not in {package, temp_path}:
                    zf.write(path, path.relative_to(source_dir).as_posix())
        temp_path.replace(package)
    finally:
        temp_path.unlink(missing_ok=True)
    meta["package"] = package.name
    meta["files"] = sorted({*meta.get("files", []), package.name})
    write_metadata(run_id, meta)
    return package


def delete_run(run_id: str) -> None:
    path = run_dir(run_id)
    if not path.exists():
        raise LibraryError(f"실행을 찾을 수 없습니다: {run_id}")
    shutil.rmtree(path)
    update_readme()


def update_readme() -> None:
    """artifacts/README.md를 현재 라이브러리 색인으로 재생성한다."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = ["# SPIDER 계획 라이브러리", "", "| Run | Scenario | Status | Verified | Updated |", "|---|---|---|---|---|"]
    for item in list_runs():
        rows.append(
            f"| {item.get('run_id', '-')} | {item.get('scenario_id') or '-'} | "
            f"{item.get('status', 'draft')} | {item.get('success')} | {item.get('updated_at', '-')} |"
        )
    if (ARTIFACTS_DIR / "ui-review.md").is_file():
        rows.extend(["", "## UI 검토", "", "- [실사형 항공우주 관제 UI 검토](ui-review.md)", "- [최종 검토 화면](ui-review.png)"])
    (ARTIFACTS_DIR / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

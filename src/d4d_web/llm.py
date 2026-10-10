"""OpenAI/Bedrock/비활성 LLM 공급자와 결정론 폴백."""
from __future__ import annotations

import json
import os
import copy
import re
from dataclasses import dataclass
from typing import Any, Protocol


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> str: ...


@dataclass
class NoneProvider:
    name: str = "none"

    def complete(self, system: str, prompt: str) -> str:
        raise RuntimeError("LLM 공급자가 비활성화되어 있습니다.")


@dataclass
class OpenAIProvider:
    model: str = "gpt-4o-mini"
    name: str = "openai"

    def complete(self, system: str, prompt: str) -> str:
        from openai import OpenAI  # 선택 의존성

        response = OpenAI().responses.create(
            model=self.model,
            instructions=system,
            input=prompt,
        )
        return response.output_text


@dataclass
class BedrockProvider:
    model: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    region: str | None = None
    name: str = "bedrock"

    def complete(self, system: str, prompt: str) -> str:
        import boto3  # 선택 의존성

        client = boto3.client("bedrock-runtime", region_name=self.region or os.getenv("AWS_REGION"))
        response = client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 1200},
        )
        return response["output"]["message"]["content"][0]["text"]


def get_provider(name: str | None = None) -> LLMProvider:
    selected = (name or os.getenv("D4D_LLM_PROVIDER", "none")).lower()
    if selected == "openai" and os.getenv("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401

            return OpenAIProvider(os.getenv("D4D_OPENAI_MODEL", "gpt-4o-mini"))
        except ImportError:
            pass
    if selected == "bedrock":
        try:
            import boto3  # noqa: F401

            session = boto3.Session()
            if session.get_credentials() is not None:
                return BedrockProvider(
                    os.getenv("D4D_BEDROCK_MODEL", "anthropic.claude-3-5-sonnet-20240620-v1:0"),
                    os.getenv("AWS_REGION"),
                )
        except Exception:
            pass
    return NoneProvider()


def deterministic_patch(instruction: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """LLM이 없어도 재현 가능한 자연어 패치 제안을 반환한다."""
    text = instruction.strip()
    lower = text.lower()
    patch: list[dict[str, Any]] = []
    policy = None
    if any(word in lower for word in ("continue_mission", "계속", "통과 허용")):
        policy = "continue_mission"
    elif any(word in lower for word in ("abort", "중단", "통과 금지")):
        policy = "abort"
    if policy:
        patch.append({"op": "replace", "path": "/link_policy", "value": policy})
    if "해상도" in text and "0.5" in text:
        patch.append({"op": "replace", "path": "/field/resolution_m", "value": 0.5})
    shelter_match = re.search(r"노드\s*(\d+)\s*(?:을|를)\s*대피소로", text)
    if shelter_match:
        for index, tag in enumerate(scenario.get("tags") or []):
            if str(tag.get("id")) == shelter_match.group(1):
                patch.append({"op": "add", "path": f"/tags/{index}/name", "value": "대피소"})
                break
    if "사진" in text:
        size_match = re.search(r"(\d+)\s*(?:kb|KB|킬로)", text)
        size = int(size_match.group(1)) * 1024 if size_match else 30 * 1024
        node_match = re.search(r"노드\s*(\d+)", text)
        source = int(node_match.group(1)) if node_match else "start"
        messages = scenario.get("messages") or []
        priority = max([int(m.get("priority", 1)) for m in messages] or [0]) + 1
        patch.append(
            {
                "op": "add",
                "path": "/messages/-",
                "value": {
                    "name": "피해평가_사진",
                    "from": source,
                    "to": "goal",
                    "bytes": size,
                    "priority": priority,
                },
            }
        )
    if "광학" in text or "메일박스" in text:
        for index, tag in enumerate(scenario.get("tags") or []):
            if tag.get("role") == "ble":
                patch.append({"op": "replace", "path": f"/tags/{index}/role", "value": "mailbox"})
    if not patch:
        patch.append({"op": "note", "path": "/", "value": text or "변경 요청 없음"})
    preview, diff = apply_patch_preview(scenario, patch)
    return {
        "provider": "none",
        "patch": patch,
        "scenario": preview,
        "diff": diff,
        "summary": f"{scenario.get('name', 'unnamed')} 시나리오에 대한 결정론적 패치 제안",
        "applied": False,
    }


def apply_patch_preview(scenario: dict[str, Any], patch: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """UI 검토용으로 제한된 JSON Patch(add/replace)를 안전하게 적용한다."""
    result = copy.deepcopy(scenario)
    diff: list[str] = []
    for operation in patch:
        if operation.get("op") not in {"add", "replace"}:
            continue
        path = str(operation.get("path", ""))
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path.strip("/").split("/") if p]
        if not parts:
            continue
        parent: Any = result
        try:
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            leaf, value = parts[-1], copy.deepcopy(operation.get("value"))
            if isinstance(parent, list):
                if leaf == "-":
                    parent.append(value)
                else:
                    parent[int(leaf)] = value
            else:
                parent[leaf] = value
            diff.append(f"+ {path}: {json.dumps(value, ensure_ascii=False)}")
        except (KeyError, IndexError, TypeError, ValueError):
            diff.append(f"! 적용 불가 {path}")
    return result, diff


def deterministic_briefing(scenario: dict[str, Any], analysis: dict[str, Any] | None = None) -> str:
    field = scenario.get("field", {})
    objectives = scenario.get("objectives", [])
    zones = scenario.get("zones", [])
    tags = scenario.get("tags", [])
    lines = [
        f"작전명 {scenario.get('name', 'unnamed')}.",
        f"작전 구역은 {field.get('width_m', '?')}m × {field.get('height_m', '?')}m이며 목표 {len(objectives)}개, "
        f"위험 구역 {len(zones)}개, 태그 {len(tags)}개가 등록되어 있습니다.",
        f"링크 정책은 {scenario.get('link_policy', 'abort')}입니다.",
    ]
    if analysis:
        lines.append(
            f"분석 결과 지도상 통과 영역 {analysis.get('free_frac', 0):.1%}, "
            f"재밍 비율 {analysis.get('jam_frac', 0):.1%}입니다."
        )
        blockers = analysis.get("blocking") or []
        lines.append("차단 요인: " + (" / ".join(blockers) if blockers else "없음"))
    return " ".join(lines)


def patch_with_llm(instruction: str, scenario: dict[str, Any], provider_name: str | None = None) -> dict[str, Any]:
    provider = get_provider(provider_name)
    if provider.name == "none":
        return deterministic_patch(instruction, scenario)
    prompt = json.dumps({"instruction": instruction, "scenario": scenario}, ensure_ascii=False)
    try:
        text = provider.complete(
            "JSON Patch 배열과 짧은 summary를 JSON 객체로만 반환하세요. 위험한 자동 적용은 하지 마세요.",
            prompt,
        )
        result = json.loads(text)
        preview, diff = apply_patch_preview(scenario, result.get("patch") or [])
        result.update(scenario=preview, diff=diff)
        result.update(provider=provider.name, applied=False)
        return result
    except Exception:
        return deterministic_patch(instruction, scenario)


def briefing_with_llm(
    scenario: dict[str, Any], analysis: dict[str, Any] | None = None, provider_name: str | None = None
) -> dict[str, str]:
    provider = get_provider(provider_name)
    fallback = deterministic_briefing(scenario, analysis)
    if provider.name == "none":
        return {"provider": "none", "briefing": fallback}
    try:
        prompt = json.dumps({"scenario": scenario, "analysis": analysis}, ensure_ascii=False)
        text = provider.complete("한국어 작전 브리핑을 사실 중심으로 5문장 이내 작성하세요.", prompt)
        return {"provider": provider.name, "briefing": text}
    except Exception:
        return {"provider": "none", "briefing": fallback}

import { describe, expect, it } from "vitest";

import { buildPlanRequest } from "./scenarioRequest";

describe("계획 요청의 시나리오 원본 결합", () => {
  const scenario = { name: "시연", field: { width_m: 1 }, tags: [] };

  it("편집하지 않은 원격 원본은 ID만 보내 서버 원본으로 계획한다", () => {
    const payload = buildPlanRequest("remote", "demo_spider", false, scenario);
    expect(payload).toEqual({ scenario_id: "demo_spider" });
    // 서버의 1.0이 브라우저에서 1로 직렬화되어도 그 복사본을 원본 ID에 결합하지 않는다.
    expect(JSON.stringify(payload)).not.toContain("scenario\"");
  });

  it("편집한 원격 변형은 현재 시나리오만 보내 원본 ID에 결합하지 않는다", () => {
    const payload = buildPlanRequest("remote", "demo_spider", true, scenario);
    expect(payload).toEqual({ scenario });
    expect(payload.scenario).toBe(scenario);
    expect(payload).not.toHaveProperty("scenario_id");
  });

  it("내장 시나리오는 키가 유효해도 시나리오만 보낸다", () => {
    expect(buildPlanRequest("builtin", "demo_spider", false, scenario)).toEqual({ scenario });
  });

  it.each(["", ".", "..", "../demo_spider", "demo/spider", "시나리오", " demo", "demo\n", "a".repeat(129)])("부적합 ID %j는 원본에 결합하지 않는다", (id) => {
    expect(buildPlanRequest("remote", id, false, scenario)).toEqual({ scenario });
  });

  it.each(["a", "0", "demo-spider.v2_1", "a".repeat(128)])("유효 ID %j는 원본 ID만 보낸다", (id) => {
    expect(buildPlanRequest("remote", id, false, scenario)).toEqual({ scenario_id: id });
  });
});

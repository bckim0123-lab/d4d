const SCENARIO_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export type PlanScenarioRequest<T> =
  | { scenario_id: string; scenario?: never }
  | { scenario: T; scenario_id?: never };

/**
 * 편집하지 않은 원격 원본은 서버에서 직접 읽는다. 브라우저가 JSON 숫자를
 * 재직렬화한 복사본을 원본 ID와 함께 보내면 승인 시 원본 해시와 달라질 수 있다.
 * 편집본·내장 시나리오는 ID 없는 변형으로 보내며 서버의 승인 해시 검사는 유지한다.
 */
export function buildPlanRequest<T>(
  source: "builtin" | "remote",
  scenarioKey: string,
  dirty: boolean,
  scenario: T,
): PlanScenarioRequest<T> {
  const validId = SCENARIO_ID.exec(scenarioKey)?.[0] === scenarioKey;
  if (source === "remote" && !dirty && validId) return { scenario_id: scenarioKey };
  return { scenario };
}

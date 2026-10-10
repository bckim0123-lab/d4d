import { describe, expect, it } from "vitest";

import {
  ApproachController,
  ApproachSimulation,
  HandoverModel,
  DEMO_SCENARIOS,
  DemoScript,
  defaultSimParams,
  defaultPlantParams,
  scenarioParams,
  summarize,
  type ControllerInput,
} from "./approachSim";

function healthy(nowMs: number): ControllerInput {
  return {
    nowMs, enabled: true, authorityAuto: true, kill: false,
    tagValid: true, tagAtMs: nowMs, errRightCm: 0, errFwdCm: 100,
    velValid: true, velAtMs: nowMs, velRightCmS: 0, velFwdCmS: 0, flowQuality: 58,
    heightValid: true, heightAtMs: nowMs, heightCm: 120,
    maskKnown: true, maskOk: true,
  };
}

function runScenario(id: string) {
  const scenario = DEMO_SCENARIOS.find((s) => s.id === id)!;
  const sim = new ApproachSimulation(scenario.startXcm, scenario.startYcm, scenarioParams(scenario));
  const script = new DemoScript(scenario);
  while (!script.finished(sim)) {
    script.apply(sim);
    sim.step(0.05);
  }
  return { sim, scenario };
}

describe("approach controller port", () => {
  it("matches the firmware speed profile (distance-proportional between stop and slow radius)", () => {
    const c = new ApproachController();
    expect(c.targetSpeed(100)).toBe(10);
    expect(c.targetSpeed(25)).toBe(10);
    expect(c.targetSpeed(15)).toBeCloseTo(6, 4);
    expect(c.targetSpeed(5.01)).toBeCloseTo(2.004, 3);
    expect(c.targetSpeed(5)).toBe(0);
    expect(c.targetSpeed(0)).toBe(0);
  });

  it("limits target-velocity acceleration to 0.10 m/s²", () => {
    const c = new ApproachController();
    const first = c.step(healthy(0), 0.05);
    expect(first.vTargetFwdCmS).toBeCloseTo(0.5, 4);
    let out = first;
    for (let i = 1; i <= 20; i++) out = c.step(healthy(i * 50), 0.05);
    expect(out.vTargetFwdCmS).toBeCloseTo(10, 3);
    expect(out.state).toBe("APPROACH");
  });

  it("reports faults in firmware priority order and drives commands back to zero", () => {
    const c = new ApproachController();
    for (let i = 0; i < 40; i++) c.step(healthy(i * 50), 0.05);
    const manual = c.step({ ...healthy(2000), authorityAuto: false }, 0.05);
    expect(manual.state).toBe("FAULT");
    expect(manual.fault).toBe("manual_authority");
    expect(c.step({ ...healthy(2050), kill: true }, 0.05).fault).toBe("kill");
    expect(c.step({ ...healthy(2100), heightCm: 10 }, 0.05).fault).toBe("height");
    expect(c.step({ ...healthy(2150), flowQuality: 20 }, 0.05).fault).toBe("flow");
    expect(c.step({ ...healthy(2200), velFwdCmS: 90 }, 0.05).fault).toBe("velocity");
    let out = manual;
    for (let i = 0; i < 40; i++) out = c.step({ ...healthy(2250 + i * 50), authorityAuto: false }, 0.05);
    expect(out.rollUs).toBe(0);
    expect(out.pitchUs).toBe(0);
  });

  it("brakes to HOLD when the tag goes stale but flow is still valid", () => {
    const c = new ApproachController();
    c.p.lostNeutral = 0; // -25의 선택적 광류 제동 방식
    for (let i = 0; i < 40; i++) c.step(healthy(i * 50), 0.05);
    let lost = c.step({ ...healthy(2000), tagAtMs: 1000, velFwdCmS: 8 }, 0.05);
    expect(lost.state).toBe("HOLD");
    expect(lost.fault).toBe("tag_lost");
    for (let i = 1; i <= 30; i++) lost = c.step({ ...healthy(2000 + i * 50), tagAtMs: 1000, velFwdCmS: 8 }, 0.05);
    expect(lost.vTargetFwdCmS).toBeCloseTo(0, 3);
    expect(lost.pitchUs).toBeLessThan(-10); // 앞으로 가는 중이니 뒤로 당긴다
  });

  it("uses firmware -25 defaults", () => {
    expect(defaultSimParams()).toMatchObject({ deadbandNeutral: 1, lostNeutral: 1, overspeedCmS: 12, handoverSlewUsPerS: 100 });
  });

  it("neutralizes tag loss by default without generating a braking command", () => {
    const c = new ApproachController();
    for (let i = 0; i < 40; i++) c.step(healthy(i * 50), 0.05);
    let out = c.step({ ...healthy(2000), tagAtMs: 1000, velFwdCmS: 10 }, 0.05);
    expect(out).toMatchObject({ state: "HOLD", fault: "tag_lost", neutral: true, vTargetFwdCmS: 0 });
    expect(out.pitchUs).toBeGreaterThanOrEqual(0);
    for (let i = 1; i <= 40; i++) out = c.step({ ...healthy(2000 + i * 50), tagAtMs: 1000, velFwdCmS: 10 }, 0.05);
    expect(out.pitchUs).toBe(0);
    expect(out.rollUs).toBe(0);
  });

  it("neutralizes within the deadband and resumes outside it", () => {
    const c = new ApproachController();
    c.p.cmdSlewUsPerS = 10000;
    const inside = { ...healthy(0), errFwdCm: 4, velFwdCmS: 6 };
    expect(c.step(inside, 0.05)).toMatchObject({ state: "ARRIVING", neutral: true, pitchUs: 0, rollUs: 0 });
    const braking = new ApproachController({ ...defaultSimParams(), deadbandNeutral: 0, cmdSlewUsPerS: 10000 });
    expect(braking.step(inside, 0.05).pitchUs).toBeLessThan(-5);
    const outside = c.step({ ...healthy(50), errFwdCm: 15 }, 0.05);
    expect(outside.neutral).toBe(false);
    expect(outside.targetSpeedCmS).toBeGreaterThan(0);
  });

  it("brakes measured overspeed, recovers, and preserves the firmware neutral priority", () => {
    const c = new ApproachController({ ...defaultSimParams(), maxAccelCmS2: 1000 });
    const fast = c.step({ ...healthy(0), velFwdCmS: 15 }, 0.05);
    expect(fast).toMatchObject({ overspeed: true, neutral: false, vTargetFwdCmS: 0 });
    expect(fast.pitchUs).toBeLessThan(0);
    expect(c.step({ ...healthy(50), velFwdCmS: 8 }, 0.05).overspeed).toBe(false);
    expect(c.step({ ...healthy(100), errFwdCm: 4, velFwdCmS: 15 }, 0.05)).toMatchObject({ neutral: true, overspeed: false });
    expect(c.step({ ...healthy(150), tagValid: false, velFwdCmS: 15 }, 0.05)).toMatchObject({ neutral: true, overspeed: false });
  });

  it("matches every firmware fault condition and its precedence", () => {
    const cases: [Partial<ControllerInput>, string][] = [
      [{ enabled: false, authorityAuto: false, kill: true }, "disabled"],
      [{ authorityAuto: false, kill: true }, "manual_authority"],
      [{ kill: true, maskKnown: false }, "kill"],
      [{ maskKnown: false, maskOk: false, heightCm: 10 }, "mask_unknown"],
      [{ maskOk: false, heightCm: 10 }, "mask"],
      [{ heightCm: 10, flowQuality: 10 }, "height"],
      [{ heightCm: 200 }, "height"], [{ heightAtMs: 0 }, "height"], [{ heightValid: false }, "height"],
      [{ flowQuality: 10, velFwdCmS: 90 }, "flow"], [{ velAtMs: 0 }, "flow"], [{ velValid: false }, "flow"],
      [{ velFwdCmS: 90 }, "velocity"],
    ];
    for (const [mutation, fault] of cases) {
      const c = new ApproachController({ ...defaultSimParams(), maxAccelCmS2: 1000 });
      for (let i = 0; i < 10; i++) c.step(healthy(1000), 0.05);
      const out = c.step({ ...healthy(1000), ...mutation }, 0.05);
      expect(out, fault).toMatchObject({ state: fault === "disabled" ? "IDLE" : "FAULT", fault, vTargetFwdCmS: 0, neutral: false, overspeed: false });
      expect(out.pitchUs, fault).toBeCloseTo(15);
    }
  });

  it("matches command slew, signs and clamp from the host tests", () => {
    const c = new ApproachController({ ...defaultSimParams(), maxAccelCmS2: 1000 });
    const input = { ...healthy(0), errRightCm: 100, errFwdCm: 0 };
    expect(c.step(input, 0.05).rollUs).toBe(5);
    for (let i = 0; i < 10; i++) c.step(input, 0.05);
    c.p.rollSign = -1;
    expect(c.step(input, 0.05).rollUs).toBe(-20);
    c.p.velKpUsPerCmS = 50;
    c.p.cmdSlewUsPerS = 10000;
    expect(c.step(input, 0.05).rollUs).toBe(-80);
  });

  it("requires the arrival dwell and resumes when pushed out", () => {
    const c = new ApproachController();
    let out = c.step({ ...healthy(0), errFwdCm: 3 }, 0.05);
    expect(out.state).toBe("ARRIVING");
    for (let i = 1; i < 40; i++) out = c.step({ ...healthy(i * 50), errFwdCm: 3 }, 0.05);
    expect(out).toMatchObject({ state: "ARRIVED", neutral: true, arriveHeldMs: 2000 });
    expect(c.step({ ...healthy(2000), errFwdCm: 10 }, 0.05).state).toBe("ARRIVING");
    c.reset();
    for (let i = 0; i < 60; i++) out = c.step({ ...healthy(i * 50), errFwdCm: 3, velFwdCmS: 6 }, 0.05);
    expect(out).toMatchObject({ state: "ARRIVING", arriveHeldMs: 0 });
  });

  it("matches the firmware frictionless one-dimensional arrival model", () => {
    const c = new ApproachController({ ...defaultSimParams(), deadbandNeutral: 0 });
    let pos = 0; let vel = 0; let acc = 0; let maxVel = 0; let overshoot = 0;
    let out = c.step(healthy(0), 0.05);
    c.reset();
    for (let i = 1; i <= 2400; i++) {
      out = c.step({ ...healthy(i * 50), errFwdCm: 100 - pos, velFwdCmS: vel }, 0.05);
      acc += (out.pitchUs - acc) * (0.05 / 0.3);
      vel += acc * 0.05;
      pos += vel * 0.05;
      maxVel = Math.max(maxVel, vel);
      overshoot = Math.max(overshoot, pos - 100);
    }
    expect(maxVel).toBeLessThanOrEqual(11);
    expect(overshoot).toBeLessThanOrEqual(2);
    expect(Math.abs(100 - pos)).toBeLessThanOrEqual(5);
    expect(out.state).toBe("ARRIVED");
  });

  it("fails closed on unknown or invalid masks in the plant model", () => {
    const sim = new ApproachSimulation(10, -20, defaultSimParams(), { ...defaultPlantParams(), tagNoiseCm: 0, flowNoiseCmS: 0 });
    sim.disturbance.maskKnown = false;
    expect(sim.step(0.05).out.fault).toBe("mask_unknown");
    sim.disturbance.maskKnown = true;
    sim.disturbance.maskOk = false;
    expect(sim.step(0.05).out.fault).toBe("mask");
  });
});

describe("demo scenarios", () => {
  it("captures the pilot baseline at AUTO entry and slews all RPY axes to 1500", () => {
    const handover = new HandoverModel();
    expect(handover.step(false, [1520, 1485, 1510], 0.05, 100)).toEqual([1520, 1485, 1510]);
    expect(handover.step(true, [1520, 1485, 1510], 0.05, 100)).toEqual([1520, 1485, 1510]);
    expect(handover.step(true, [1400, 1600, 1500], 0.05, 100)).toEqual([1515, 1490, 1505]);
    for (let i = 0; i < 4; i++) handover.step(true, [1400, 1600, 1500], 0.05, 100);
    expect(handover.step(true, [1400, 1600, 1500], 0.05, 100)).toEqual([1500, 1500, 1500]);
    expect(handover.step(false, [1400, 1600, 1500], 0.05, 100)).toEqual([1400, 1600, 1500]);
    handover.reset();
    expect(handover.step(true, [1400, 1600, 1500], 0.05, 100)).toEqual([1500, 1500, 1500]);
  });

  it("visualizes handover with a fixed AUTO baseline despite later pilot movement", () => {
    const { sim } = runScenario("handover");
    const manual = sim.frames.filter((f) => !f.disturbance.authorityAuto);
    expect(manual[manual.length - 1].baseRpy).toEqual([1520, 1485, 1510]);
    const auto = sim.frames.filter((f) => f.disturbance.authorityAuto);
    expect(auto[0].baseRpy).toEqual([1520, 1485, 1510]);
    expect(auto.find((f) => f.tMs >= 3500)!.baseRpy).toEqual([1500, 1500, 1500]);
    expect(auto[auto.length - 1].disturbance.pilotRpy).toEqual([1480, 1520, 1490]);
  });

  it("nominal approach arrives inside 5cm under the 10cm/s cap without overshoot", () => {
    const { sim } = runScenario("nominal");
    const s = summarize(sim.frames);
    expect(s.arrived).toBe(true);
    expect(s.arrivedAtMs).not.toBeNull();
    expect(s.maxSpeedCmS).toBeLessThanOrEqual(11.5);
    expect(s.finalDistCm).toBeLessThanOrEqual(5);
    expect(s.faultCount).toBe(0);
    expect(s.maxCmdUs).toBeLessThanOrEqual(defaultSimParams().cmdMaxUs);
  });

  it("tag-lost scenario passes through HOLD and still arrives", () => {
    const { sim } = runScenario("tag-lost");
    const states = new Set(sim.frames.map((f) => f.out.state));
    expect(states.has("HOLD")).toBe(true);
    const holdFrames = sim.frames.filter((f) => f.out.state === "HOLD");
    const lastHold = holdFrames[holdFrames.length - 1];
    expect(lastHold.out.vTargetRightCmS).toBeCloseTo(0, 3); // 제동이 끝나면 목표 속도 0
    expect(Math.hypot(lastHold.vx, lastHold.vy)).toBeLessThan(2); // 실제로 거의 멈춤
    expect(summarize(sim.frames).arrived).toBe(true);
  });

  it("manual override zeroes the board command and the aircraft stops being driven", () => {
    const { sim } = runScenario("manual");
    const manual = sim.frames.filter((f) => !f.disturbance.authorityAuto);
    expect(manual.length).toBeGreaterThan(50);
    expect(manual[0].out.fault).toBe("manual_authority");
    const settled = manual[manual.length - 1];
    expect(settled.out.rollUs).toBe(0);
    expect(settled.out.pitchUs).toBe(0);
    expect(summarize(sim.frames).arrived).toBe(true);
  });

  it("flow degradation stops the approach until quality recovers", () => {
    const { sim } = runScenario("flow-degraded");
    const degraded = sim.frames.filter((f) => f.disturbance.flowQuality < 40);
    expect(degraded.every((f) => f.out.state === "FAULT" && f.out.fault === "flow")).toBe(true);
    expect(summarize(sim.frames).arrived).toBe(true);
  });

  it("wind scenario arrives with the velocity I term and would not with P only", () => {
    const { sim } = runScenario("wind");
    const s = summarize(sim.frames);
    expect(s.arrived).toBe(true);
    expect(s.maxSpeedCmS).toBeLessThan(60);

    const scenario = DEMO_SCENARIOS.find((x) => x.id === "wind")!;
    const pOnly = new ApproachSimulation(scenario.startXcm, scenario.startYcm, { ...scenarioParams(scenario), velKiUsPerCm: 0 });
    const script = new DemoScript(scenario);
    while (!script.finished(pOnly)) { script.apply(pOnly); pOnly.step(0.05); }
    expect(summarize(pOnly.frames).arrived).toBe(false);
  });

  it("is deterministic for the same seed", () => {
    const a = runScenario("nominal").sim.frames.map((f) => [f.x, f.y]);
    const b = runScenario("nominal").sim.frames.map((f) => [f.x, f.y]);
    expect(a).toEqual(b);
  });

  it("shows divergence when the roll sign is wrong (what the ground test must catch)", () => {
    const params = { ...defaultSimParams(), rollSign: -1 };
    const sim = new ApproachSimulation(40, 0, params);
    for (let i = 0; i < 200; i++) sim.step(0.05);
    expect(Math.abs(sim.x)).toBeGreaterThan(40);
  });

  it("starts every demo scenario with the tag inside the camera footprint", () => {
    for (const scenario of DEMO_SCENARIOS) {
      const sim = new ApproachSimulation(scenario.startXcm, scenario.startYcm);
      expect(sim.tagInView(), scenario.id).toBe(true);
    }
    // 50cm 높이에서는 26cm 밖이 보이지 않는다 — 실기 시험 배치에 그대로 적용되는 제약
    const low = new ApproachSimulation(30, 0);
    low.plant = { ...low.plant, heightCm: 50 };
    expect(low.fovRadiusCm()).toBeCloseTo(26.0, 0);
    expect(low.tagInView()).toBe(false);
  });
});

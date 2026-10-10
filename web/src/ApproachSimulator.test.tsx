import { describe, expect, it } from "vitest";
import { ApproachSimulation, DEMO_SCENARIOS, DemoScript, scenarioParams } from "./approachSim";
import { replaySimulation } from "./ApproachSimulator";

describe("simulator timeline replay", () => {
  const scenario = DEMO_SCENARIOS[0];

  it("clamps negative/end seeks and keeps exact 50ms physics steps", () => {
    const params = scenarioParams(scenario);
    const start = replaySimulation(scenario, params, [], -500);
    expect(start.sim.tMs).toBe(0);
    expect(start.sim.frames).toEqual([]);
    expect(replaySimulation(scenario, params, [], 1234).sim.tMs).toBe(1200);
    const end = replaySimulation(scenario, params, [], scenario.durationMs + 1000);
    expect(end.sim.tMs).toBe(scenario.durationMs);
    expect(end.script.finished(end.sim)).toBe(true);
  });

  it.each(DEMO_SCENARIOS.map((item) => [item.id, item] as const))("reproduces forward physics exactly for %s", (_id, current) => {
    const params = scenarioParams(current);
    const forward = new ApproachSimulation(current.startXcm, current.startYcm, { ...params });
    const script = new DemoScript(current);
    while (!script.finished(forward)) { script.apply(forward); forward.step(0.05); }
    const replay = replaySimulation(current, params, [], current.durationMs);
    expect(replay.sim.frames).toEqual(forward.frames);
  });

  it("replays parameter changes and switches from scripted to manual disturbances at the recorded time", () => {
    const params = scenarioParams(scenario);
    const events = [
      { atMs: 1000, params: { maxSpeedCmS: 6 } },
      { atMs: 2000, disturbance: { windRightCmS2: 5 } },
      { atMs: 4000, disturbance: { kill: true } },
    ];
    const forward = new ApproachSimulation(scenario.startXcm, scenario.startYcm, { ...params });
    const script = new DemoScript(scenario);
    while (forward.tMs < 6000) {
      if (forward.tMs === 1000) forward.ctrl.p.maxSpeedCmS = 6;
      if (forward.tMs === 2000) forward.disturbance.windRightCmS2 = 5;
      if (forward.tMs === 4000) forward.disturbance.kill = true;
      if (forward.tMs < 2000) script.apply(forward);
      forward.step(0.05);
    }
    const replay = replaySimulation(scenario, params, events, 6000);
    expect(replay.sim.frames).toEqual(forward.frames);
    expect(replay.manual).toBe(true);
    expect(replay.sim.frames.at(-1)?.out.fault).toBe("kill");
    expect(params.maxSpeedCmS).toBe(10);
  });

  it("shows edits at a paused seek immediately without advancing the physics", () => {
    const params = scenarioParams(scenario);
    const events = [{ atMs: 1500, disturbance: { tagOccluded: true, pilotRpy: [1510, 1500, 1490] as [number, number, number] } }];
    const before = replaySimulation(scenario, params, events, 1450);
    expect(before.sim.disturbance.tagOccluded).toBe(false);
    const paused = replaySimulation(scenario, params, events, 1500);
    expect(paused.sim.tMs).toBe(1500);
    expect(paused.sim.disturbance.tagOccluded).toBe(true);
    expect(paused.sim.frames.at(-1)?.disturbance.tagOccluded).toBe(false);
    paused.sim.disturbance.pilotRpy[0] = 1700;
    expect(events[0].disturbance.pilotRpy[0]).toBe(1510);
    const replay = replaySimulation(scenario, params, events, 1500);
    expect(replay.sim.disturbance.pilotRpy[0]).toBe(1510);
  });
});

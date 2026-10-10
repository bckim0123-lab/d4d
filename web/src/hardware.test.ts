import { describe, expect, expectTypeOf, it } from "vitest";
import { readFileSync } from "node:fs";

import {
  APPROACH_PARAMS,
  BOARD_COMMANDS,
  NUS_RX_UUID,
  NUS_SERVICE_UUID,
  NUS_TX_UUID,
  consumeTelemetryChunk,
  createTelemetryState,
  deriveApproachView,
  deriveAuthorityView,
  deriveBoardLinkState,
  deriveControlPanelState,
  deriveFlightLockReasons,
  deriveMtfHealth,
  deriveRcComparison,
  deriveBoardThrottle,
  deriveBoardCommandAvailability,
  freshness,
  hardwareErrorMessage,
  serializeApproachSet,
  serializeBoardCommand,
  type FcStatus,
  type FlowTelemetry,
} from "./hardware";


describe("NUS telemetry parser", () => {
  it("reads the checked-in firmware -25 samples with nullable typed fields", () => {
    const samples = readFileSync(new URL("../../hardware/reference/firmware-25/telemetry-samples.jsonl", import.meta.url), "utf8");
    const state = consumeTelemetryChunk(createTelemetryState(), `${samples}\n`, 100);
    expect(state.errors).toHaveLength(0);
    expect(state.latest.msp).toMatchObject({ tx_hz: 50, tx_always: true, rcmap: "AETR", thr_slot: 2, thr_slot_us: 1000, mask_ok: null });
    expect(state.latest.approach).toMatchObject({ neutral: false, overspeed: false, vz_cm_s: null, out_rpy: [1500, 1500, 1500], rc_tx: true });
    expect(state.latest.approach_params?.params).toMatchObject({ deadband_neutral: 1, lost_neutral: 1, overspeed: 12, handover_slew: 100 });
    expectTypeOf(state.latest.msp?.mask_ok).toEqualTypeOf<boolean | null | undefined>();
    expectTypeOf(state.latest.approach?.vz_cm_s).toEqualTypeOf<number | null | undefined>();
  });

  it("accepts authority handover and vertical-speed warning events without changing throttle", () => {
    const state = consumeTelemetryChunk(createTelemetryState(), [
      '{"type":"authority","to":"auto","ch6":1000,"t":5000,"frame_age_ms":12,"tx_hz":50,"out_rpy":[1520,1490,1505],"thr_slot_us":1000,"pilot_rpy":[1520,1490,1505],"pilot_valid":true,"fc_rc":[1520,1490,1505,1000],"approach":"on"}',
      '{"type":"warn","code":"vz","vz_cm_s":13,"limit_cm_s":10,"height_cm":50,"height_src":"fc","throttle_action":"none"}',
      '{"type":"approach_mode","mode":"on","fc_mask":15,"warning":"mask"}',
      "",
    ].join("\n"), 100);
    expect(state.errors).toHaveLength(0);
    expect(state.latest.authority).toMatchObject({ to: "auto", frame_age_ms: 12, thr_slot_us: 1000, receivedAt: 100 });
    expect(state.latest.warn).toMatchObject({ code: "vz", throttle_action: "none", limit_cm_s: 10 });
    expect(state.latest.approach_mode).toMatchObject({ fc_mask: 15, warning: "mask" });
  });
  it("buffers line fragments across BLE notifications", () => {
    let state = createTelemetryState();
    state = consumeTelemetryChunk(state, '{"type":"msp","sent":', 100);
    expect(state.buffer).toBe('{"type":"msp","sent":');
    expect(state.latest.msp).toBeUndefined();

    state = consumeTelemetryChunk(
      state,
      '42,"out":[1510,1490],"fc_link":true}\n',
      110,
    );
    expect(state.buffer).toBe("");
    expect(state.latest.msp?.sent).toBe(42);
    expect(state.latest.msp?.receivedAt).toBe(110);
  });

  it("retains the latest value independently for each telemetry type", () => {
    let state = createTelemetryState();
    state = consumeTelemetryChunk(
      state,
      [
        '{"type":"vision","fps":4}',
        '{"type":"msp","sent":1}',
        '{"type":"vision","fps":6}',
        "",
      ].join("\n"),
      200,
    );
    expect(state.latest.vision?.fps).toBe(6);
    expect(state.latest.msp?.sent).toBe(1);
  });

  it("preserves typed CH6 authority and active RC transmission state", () => {
    const state = consumeTelemetryChunk(
      createTelemetryState(),
      '{"type":"msp","authority":"manual","authority_fresh":true,"ch5":1000,"ch6":2000,"rc_throttle":1000,"rc_tx":false,"kill":false,"kill_tx":false}\n',
      250,
    );
    expect(state.latest.msp?.authority).toBe("manual");
    expect(state.latest.msp?.authority_fresh).toBe(true);
    expect(state.latest.msp?.ch6).toBe(2000);
    expect(state.latest.msp?.rc_tx).toBe(false);
    expectTypeOf(state.latest.msp?.authority).toEqualTypeOf<
      "auto" | "manual" | undefined
    >();
  });

  it("accepts firmware fc, motortest and kill lines with their real field names", () => {
    const state = consumeTelemetryChunk(
      createTelemetryState(),
      [
        '{"type":"fc","status":true,"armed":false,"arming_flags":"0x00000008","blockers":[],"battery":true,"vbat":13.59,"cells":4,"amps":0.46,"motor":[1000,1000,1000,1000],"motor_ok":true,"kill":false}',
        '{"type":"motortest","state":"stopping","reason":"manual_authority"}',
        '{"type":"kill","state":"off"}',
        "",
      ].join("\n"),
      260,
    );
    expect(state.errors).toHaveLength(0);
    expect(state.latest.fc?.vbat).toBe(13.59);
    expect(state.latest.fc?.motor).toEqual([1000, 1000, 1000, 1000]);
    expect(state.latest.fc?.blockers).toEqual([]);
    expect(state.latest.motortest?.reason).toBe("manual_authority");
    expect(state.latest.kill?.state).toBe("off");
  });

  it("reports malformed lines without discarding the next valid line", () => {
    const state = consumeTelemetryChunk(
      createTelemetryState(),
      'not-json\n{"type":"flow","connected":true,"height_cm":81}\n',
      300,
    );
    expect(state.errors).toHaveLength(1);
    expect(state.errors[0].raw).toBe("not-json");
    expect(state.latest.flow?.height_cm).toBe(81);
  });
});


describe("hardware health derivation", () => {
  const safeFc = {
    armed: false, min_check: 1100, throttle_us: 1000,
    motors_us: [1000, 1000, 1000, 1000], gates: [], mask: 71,
    can_apply_override: true, can_apply_67: true, can_restore_64: true,
  } as unknown as FcStatus;
  const msp25 = {
    type: "msp" as const, receivedAt: 100, fc_link: true,
    authority: "manual" as const, authority_fresh: true, rc_tx: true,
    tx_always: true, tx_hz: 50, rcmap: "TAER", rcmap_src: "fc" as const,
    thr_slot: 0, thr_slot_us: 1000, out: [1000, 1520, 1490, 1500], fc_rc: [1520, 1490, 1500, 1000],
  };

  it("rechecks MSP freshness at dispatch instead of trusting the previous UI tick", () => {
    const telemetry = createTelemetryState();
    telemetry.lastReceivedAt = 1000;
    telemetry.latest.msp = { ...msp25, receivedAt: 1000 };
    telemetry.latest.approach = { type: "approach", receivedAt: 1000, mode: "shadow", state: "APPROACH" };
    const args = { connected: true, telemetry, fc: safeFc, fcAt: 1000, now: 3000 };
    expect(deriveBoardCommandAvailability(args)).toMatchObject({ commandsEnabled: true, approachOutputEnabled: true });
    expect(deriveBoardCommandAvailability({ ...args, now: 3001 })).toMatchObject({ commandsEnabled: false, approachOutputEnabled: false, killEnabled: true });
  });

  it("rechecks approach and FC freshness independently immediately before dispatch", () => {
    const telemetry = createTelemetryState();
    telemetry.lastReceivedAt = 3001;
    telemetry.latest.msp = { ...msp25, receivedAt: 3001 };
    telemetry.latest.approach = { type: "approach", receivedAt: 1000, mode: "shadow", state: "APPROACH" };
    const args = { connected: true, telemetry, fc: safeFc, fcAt: 0, now: 3001 };
    expect(deriveBoardCommandAvailability(args)).toMatchObject({ commandsEnabled: true, approachOutputEnabled: false });
    telemetry.latest.approach.receivedAt = 8000;
    telemetry.latest.msp.receivedAt = 8000;
    telemetry.lastReceivedAt = 8000;
    expect(deriveBoardCommandAvailability({ ...args, now: 8000 })).toMatchObject({ commandsEnabled: true, approachOutputEnabled: true });
    expect(deriveBoardCommandAvailability({ ...args, now: 8001 })).toMatchObject({ commandsEnabled: false, approachOutputEnabled: false, killEnabled: true });
    expect(deriveBoardCommandAvailability({ ...args, now: 8000, fcQueryFailed: true }).commandsEnabled).toBe(false);
    expect(deriveBoardCommandAvailability({ ...args, connected: false }).killEnabled).toBe(false);
  });

  it("uses the reported throttle slot for non-AETR frames, including in manual mode", () => {
    expect(deriveBoardThrottle(msp25)).toMatchObject({ slot: 0, value: 1000, valid: true });
    const ready = deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: msp25, now: 100 });
    expect(ready.groundReady).toBe(true);
    const unsafe = deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: { ...msp25, out: [1500, 1520, 1000, 1500], thr_slot_us: 1500 }, now: 100 });
    expect(unsafe.groundReady).toBe(false);
    expect(unsafe.reasons.join(" ")).toContain("스로틀 슬롯");
  });

  it("fails closed for missing, malformed or contradictory firmware -25 throttle mapping", () => {
    for (const fields of [
      { thr_slot: undefined, rcmap: undefined, thr_slot_us: undefined },
      { thr_slot: 9 },
      { thr_slot: undefined, rcmap: "AAAA" },
      { thr_slot: 2 },
      { thr_slot_us: 1500 },
      { thr_slot_us: undefined, out: [] },
    ]) {
      const panel = deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: { ...msp25, ...fields }, now: 100 });
      expect(panel.groundReady).toBe(false);
    }
    expect(deriveBoardThrottle({ ...msp25, thr_slot: undefined })).toMatchObject({ slot: 0, value: 1000, valid: true });
  });

  it("blocks missing or malformed transmission flags instead of treating them as stopped", () => {
    for (const rc_tx of [undefined, null, "true", 1]) {
      const telemetry = consumeTelemetryChunk(createTelemetryState(), `${JSON.stringify({ ...msp25, rc_tx, thr_slot_us: 1500, out: [1500, 1520, 1490, 1500] })}\n`, 100);
      expect(deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: telemetry.latest.msp, now: 100 }).groundReady).toBe(false);
    }
    expect(deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: { ...msp25, rc_tx: false, thr_slot_us: 1500, out: [1500, 1520, 1490, 1500] }, now: 100 }).groundReady).toBe(true);
  });

  it("distinguishes always-on transmission from FC authority and compares mapped channels only in AUTO", () => {
    const manual = deriveAuthorityView(msp25, 100);
    expect(manual.detail).toContain("FC 미적용");
    expect(manual.detail).toContain("50Hz");
    expect(deriveRcComparison(msp25, 5, 100).available).toBe(false);
    expect(deriveRcComparison({ ...msp25, authority: "auto" }, 5, 100)).toMatchObject({ available: true, board: { roll: 1520, pitch: 1490 }, matches: true });
  });

  it("locks stale MSP even while unrelated BLE messages keep arriving", () => {
    const panel = deriveControlPanelState({ boardLink: "online", fc: safeFc, fcFresh: true, msp: msp25, now: 2101 });
    expect(panel.groundReady).toBe(false);
    expect(panel.canRestore64).toBe(false);
    expect(deriveAuthorityView({ ...msp25, authority: "auto" }, 2101).state).toBe("unknown");
    expect(deriveRcComparison({ ...msp25, authority: "auto" }, 5, 2101).available).toBe(false);
  });

  it.each([7, 64, 67, 71])("accepts safe mask %s without loosening disarm and freshness gates", (mask) => {
    const args = { boardLink: "online" as const, fc: { ...safeFc, mask }, fcFresh: true, msp: msp25, now: 100 };
    expect(deriveControlPanelState(args).canApplyOverride).toBe(true);
    expect(deriveControlPanelState({ ...args, fc: { ...args.fc, armed: true } }).canApplyOverride).toBe(false);
    expect(deriveControlPanelState({ ...args, fcFresh: false }).canRestore64).toBe(false);
  });

  it.each([8, 15, 79, 255])("rejects a throttle-containing mask %s even if a backend flag is inconsistent", (mask) => {
    expect(deriveControlPanelState({ boardLink: "online", fc: { ...safeFc, mask }, fcFresh: true, msp: msp25, now: 100 }).groundReady).toBe(false);
  });

  it("keeps RAM restore limited to fresh disarmed links while allowing recovery from high throttle", () => {
    const args = { boardLink: "online" as const, fc: { ...safeFc, throttle_us: 1500 }, fcFresh: true, msp: msp25, now: 100 };
    expect(deriveControlPanelState(args)).toMatchObject({ groundReady: false, canRestore64: true });
    expect(deriveControlPanelState({ ...args, fc: { ...args.fc, armed: true } }).canRestore64).toBe(false);
    expect(deriveControlPanelState({ ...args, msp: { ...msp25, fc_link: false } }).canRestore64).toBe(false);
  });
  it("distinguishes disconnected, stale, and online board links", () => {
    expect(deriveBoardLinkState(false, undefined, 10_000)).toBe("disconnected");
    expect(deriveBoardLinkState(true, 5_000, 10_000)).toBe("stale");
    expect(deriveBoardLinkState(true, 9_000, 10_000)).toBe("online");
  });

  it("compares board and FC roll/pitch channels", () => {
    expect(deriveRcComparison({
      type: "msp",
      receivedAt: 100,
      out: [1520, 1480],
      fc_rc: [1518, 1483],
      rc_tx: true,
      authority: "auto",
      authority_fresh: true,
    }, 5, 100)).toEqual({
      available: true,
      board: { roll: 1520, pitch: 1480 },
      fc: { roll: 1518, pitch: 1483 },
      delta: { roll: -2, pitch: 3 },
      matches: true,
    });
    expect(deriveRcComparison(undefined).available).toBe(false);
  });

  it("opens ground controls only for fresh disarmed low-throttle links", () => {
    const fc = {
      armed: false,
      mask: 64,
      min_check: 1100,
      throttle_us: 1000,
      motors_us: [1000, 1000, 1000, 1000],
      gates: [
        {
          key: "receiver_link",
          label: "수동 회수 링크",
          passed: true,
          detail: "정상",
          remediation: "",
          required_for_apply: true,
          required_for_restore: false,
        },
      ],
    } as FcStatus;
    const ready = deriveControlPanelState({
      boardLink: "online",
      fc,
      fcFresh: true,
      now: 100,
      msp: {
        type: "msp",
        receivedAt: 100,
        fc_link: true,
        authority_fresh: true,
        rc_tx: false,
      },
    });
    expect(ready.groundReady).toBe(true);
    expect(ready.actuationEnabled).toBe(true);
    expect(ready.flightLocked).toBe(true);

    for (const blocked of [
      { boardLink: "stale" as const },
      { fcFresh: false },
      { fc: { ...fc, armed: true } },
      { fc: { ...fc, throttle_us: 1100 } },
      {
        msp: {
          type: "msp" as const,
          receivedAt: 100,
          fc_link: false,
          authority_fresh: true,
          rc_tx: false,
        },
      },
    ]) {
      const state = deriveControlPanelState({
        boardLink: "online",
        fc,
        fcFresh: true,
        now: 100,
        msp: {
          type: "msp",
          receivedAt: 100,
          fc_link: true,
          authority_fresh: true,
        },
        ...blocked,
      });
      expect(state.actuationEnabled).toBe(false);
      expect(state.reasons.length).toBeGreaterThan(0);
    }
  });

  it("blocks ground control when the board sends a mid throttle in its RC frame (pre -24 firmware)", () => {
    const fc = { armed: false, mask: 64, min_check: 1100, throttle_us: 1000, motors_us: [1000, 1000, 1000, 1000], gates: [] } as unknown as FcStatus;
    const base = { type: "msp" as const, receivedAt: 100, fc_link: true, authority_fresh: true, rc_tx: true };
    const bad = deriveControlPanelState({ boardLink: "online", fc, fcFresh: true, now: 100, msp: { ...base, out: [1500, 1500, 1500, 1500, 1500, 1500, 1000, 1500] } });
    expect(bad.groundReady).toBe(false);
    expect(bad.reasons.some((r) => r.includes("스로틀 슬롯"))).toBe(true);
    const good = deriveControlPanelState({ boardLink: "online", fc, fcFresh: true, now: 100, msp: { ...base, out: [1500, 1500, 1000, 1500, 1500, 1500, 1000, 1500] } });
    expect(good.groundReady).toBe(true);
  });

  it("does not call board flow disconnected an MTF fault when FC UART4 is healthy", () => {
    const flow: FlowTelemetry = {
      type: "flow",
      connected: false,
      receivedAt: 100,
    };
    const fc = {
      sensors: ["레인지파인더", "광류(OPFLOW)"],
      serial_ports: [
        { port_label: "UART4", functions: ["MSP"] },
      ],
    } as FcStatus;
    expect(deriveMtfHealth(flow, fc)).toEqual({
      state: "healthy",
      source: "fc",
      detail: "FC UART4 MSP에서 거리·광류 감지",
    });
  });

  it("tracks fresh, stale, and missing telemetry", () => {
    expect(freshness(undefined, 10_000, 3_000)).toBe("missing");
    expect(freshness(8_000, 10_000, 3_000)).toBe("fresh");
    expect(freshness(6_000, 10_000, 3_000)).toBe("stale");
  });

  it("labels CH6 authority as auto, manual, or unknown", () => {
    expect(deriveAuthorityView({
      type: "msp", receivedAt: 1, authority: "auto", authority_fresh: true, ch6: 1000, rc_tx: true,
    }, 1)).toMatchObject({ state: "auto", tone: "cyan" });
    expect(deriveAuthorityView({
      type: "msp", receivedAt: 1, authority: "manual", authority_fresh: true, ch6: 2000, rc_tx: false,
    }, 1)).toMatchObject({ state: "manual", tone: "amber" });
    expect(deriveAuthorityView({
      type: "msp", receivedAt: 1, authority: "auto", authority_fresh: false, ch6: 1000, rc_tx: false,
    }, 1)).toMatchObject({ state: "unknown", tone: "red" });
    expect(deriveAuthorityView(undefined)).toMatchObject({ state: "unknown", label: "--" });
  });

  it("keeps the flight lock closed until every hardware blocker is cleared by hand", () => {
    const reasons = deriveFlightLockReasons({
      info: { type: "info", receivedAt: 1, build: "TAGVISION-20261010-22", axes_verified: false },
      mtf: { state: "degraded", source: "none", detail: "경로 확인" },
    });
    expect(reasons.some((reason) => reason.includes("축"))).toBe(true);
    expect(reasons.some((reason) => reason.includes("MTF"))).toBe(true);
    expect(reasons.some((reason) => reason.includes("ESC"))).toBe(true);

    const olderBuild = deriveFlightLockReasons({
      info: { type: "info", receivedAt: 1, build: "TAGVISION-20261010-20", axes_verified: true },
      mtf: { state: "healthy", source: "fc", detail: "" },
    });
    expect(olderBuild.some((reason) => reason.includes("-21"))).toBe(true);
    expect(olderBuild.some((reason) => reason.includes("축"))).toBe(false);
  });

  it("extracts structured API error details", () => {
    expect(hardwareErrorMessage({ detail: { code: "safety_gate", message: "게이트 닫힘" } }, 409))
      .toBe("게이트 닫힘");
    expect(hardwareErrorMessage({ detail: "plain" }, 400)).toBe("plain");
    expect(hardwareErrorMessage(null, 503)).toBe("백엔드 API 응답 없음 (HTTP 503)");
    expect(hardwareErrorMessage(null, 404)).toBe("HTTP 404");
  });
});


describe("board command allowlist", () => {
  it("exposes the Nordic UART UUID triplet", () => {
    expect(NUS_SERVICE_UUID).toMatch(/^6e400001-/);
    expect(NUS_RX_UUID).toMatch(/^6e400002-/);
    expect(NUS_TX_UUID).toMatch(/^6e400003-/);
  });

  it("serializes allowed commands with exactly one newline", () => {
    for (const command of [
      "info",
      "tags",
      "snap",
      "msp on",
      "msp off",
      "mission reset",
      "kill",
    ] as const) {
      expect(serializeBoardCommand(command)).toBe(`${command}\n`);
    }
  });

  it("marks kill off as confirmation-required", () => {
    expect(BOARD_COMMANDS["kill off"].confirmationRequired).toBe(true);
    expect(() => serializeBoardCommand("kill off")).toThrow(/확인/);
    expect(serializeBoardCommand("kill off", true)).toBe("kill off\n");
  });

  it.each([
    "motortest 1 5 2",
    "fcmsp 200",
    "voicecmd stop",
    "arm",
    "throttle 1500",
    "anything",
    "info\nkill",
  ])("rejects unsafe or unknown command %s", (command) => {
    expect(() => serializeBoardCommand(command)).toThrow(/허용/);
  });
});

describe("approach controller integration", () => {
  it("reports neutral HOLD, overspeed and mask faults using firmware -25 meanings", () => {
    const base = { type: "approach" as const, receivedAt: 100, mode: "on" as const, authority: "auto" as const, rc_tx: true, mask_ok: true };
    const hold = deriveApproachView({ ...base, state: "HOLD", fault: "tag_lost", neutral: true }, undefined, 100);
    expect(hold.detail).toContain("중립");
    const overspeed = deriveApproachView({ ...base, state: "APPROACH", overspeed: true }, undefined, 100);
    expect(overspeed.detail).toContain("과속");
    expect(overspeed.tone).toBe("amber");
    for (const fault of ["mask", "mask_unknown"]) {
      const view = deriveApproachView({ ...base, state: "FAULT", fault, mask_ok: fault === "mask" ? false : null }, undefined, 100);
      expect(view.tone).toBe("red");
      expect(view.detail).toContain("마스크");
      expect(view.outputsToFc).toBe(false);
    }
    expect(deriveApproachView({ ...base, state: "APPROACH" }, undefined, 100).outputsToFc).toBe(true);
    expect(deriveApproachView({ ...base, state: "APPROACH", authority: "manual" }, undefined, 100).outputsToFc).toBe(false);
    expect(deriveApproachView({ ...base, state: "APPROACH" }, undefined, 2101).outputsToFc).toBe(false);
  });

  it("allows the four new bounded firmware -25 parameters and rejects fractional toggles", () => {
    for (const [name, value] of [["deadband_neutral", 1], ["lost_neutral", 0], ["overspeed", 12], ["handover_slew", 100]] as const) {
      expect(serializeApproachSet(name, value)).toBe(`approach set ${name} ${value}\n`);
    }
    for (const [name, value] of [["deadband_neutral", 0.5], ["lost_neutral", -1], ["overspeed", 13], ["handover_slew", 0], ["handover_slew", 1001]] as const) {
      expect(() => serializeApproachSet(name, value)).toThrow();
    }
  });
  const approachLine =
    '{"type":"approach","mode":"shadow","state":"FAULT","fault":"manual_authority","dist_cm":0.0,"err_right_cm":0.0,"err_fwd_cm":0.0,"target_speed":0.0,"v_target":[0.0,0.0],"v_flow":[0.0,0.0],"v_flow_valid":true,"flow_q":17,"v_tag":[null,null],"roll_us":0.0,"pitch_us":0.0,"tag":"none","authority":"manual","ch6":2000,"height_cm":3.0,"height_valid":true,"att":[1.0,0.0],"arrive_held_ms":0}';

  it("parses the firmware -23 approach, params, mode and set lines", () => {
    const state = consumeTelemetryChunk(
      createTelemetryState(),
      [
        approachLine,
        '{"type":"approach_params","mode":"shadow","params":{"max_speed":10.0,"vel_kp":2.00,"flow_swap":0}}',
        '{"type":"approach_mode","mode":"on"}',
        '{"type":"approach_set","name":"vel_kp","value":1.500}',
        "",
      ].join("\n"),
      500,
    );
    expect(state.errors).toHaveLength(0);
    expect(state.latest.approach?.state).toBe("FAULT");
    expect(state.latest.approach?.fault).toBe("manual_authority");
    expect(state.latest.approach?.v_tag).toEqual([null, null]);
    expect(state.latest.approach?.att).toEqual([1, 0]);
    expect(state.latest.approach_params?.params?.vel_kp).toBe(2);
    expect(state.latest.approach_mode?.mode).toBe("on");
    expect(state.latest.approach_set?.value).toBe(1.5);
  });

  it("summarizes approach state and never calls a stale report safe", () => {
    const state = consumeTelemetryChunk(createTelemetryState(), `${approachLine}\n`, 1_000);
    const fresh = deriveApproachView(state.latest.approach, undefined, 1_500);
    expect(fresh.fresh).toBe(true);
    expect(fresh.mode).toBe("shadow");
    expect(fresh.state).toBe("FAULT");
    expect(fresh.tone).toBe("amber"); // 수동 제어권은 정상적인 중지 사유
    expect(fresh.outputsToFc).toBe(false);
    expect(fresh.detail).toContain("manual_authority");

    const stale = deriveApproachView(state.latest.approach, undefined, 5_000);
    expect(stale.fresh).toBe(false);
    expect(stale.state).toBe("unknown");
    expect(stale.tone).toBe("amber");

    const none = deriveApproachView(undefined, { type: "info", receivedAt: 0, approach: "off" }, 5_000);
    expect(none.mode).toBe("off");
    expect(none.tone).toBe("muted");
  });

  it("never treats an output mode alone as evidence of FC application", () => {
    const state = consumeTelemetryChunk(
      createTelemetryState(),
      '{"type":"approach","mode":"on","state":"FAULT","fault":"flow_quality","dist_cm":30}\n',
      1_000,
    );
    const view = deriveApproachView(state.latest.approach, undefined, 1_200);
    expect(view.tone).toBe("red");
    expect(view.outputsToFc).toBe(false);
    const legacy = deriveApproachView(undefined, { type: "info", receivedAt: 0, approach: "legacy" }, 0);
    expect(legacy.outputsToFc).toBe(false);
  });

  it("allows non-actuating approach commands without confirmation", () => {
    for (const command of ["approach", "approach params", "approach shadow", "approach off", "approach reset"] as const) {
      expect(BOARD_COMMANDS[command].confirmationRequired).toBe(false);
      expect(serializeBoardCommand(command)).toBe(`${command}\n`);
    }
  });

  it("requires confirmation before approach on or legacy start sending roll/pitch", () => {
    for (const command of ["approach on", "approach legacy"] as const) {
      expect(BOARD_COMMANDS[command].confirmationRequired).toBe(true);
      expect(() => serializeBoardCommand(command)).toThrow(/확인/);
      expect(serializeBoardCommand(command, true)).toBe(`${command}\n`);
    }
  });

  it("serializes approach set only for allowlisted names inside their range", () => {
    expect(serializeApproachSet("vel_kp", 1.5)).toBe("approach set vel_kp 1.5\n");
    expect(serializeApproachSet("vel_kp", "2")).toBe("approach set vel_kp 2\n");
    expect(serializeApproachSet("roll_sign", -1)).toBe("approach set roll_sign -1\n");
    expect(serializeApproachSet("cam_off_fwd", 2.25)).toBe("approach set cam_off_fwd 2.25\n");
    expect(() => serializeApproachSet("bogus", 1)).toThrow(/허용/);
    expect(() => serializeApproachSet("vel_kp", "abc")).toThrow(/숫자/);
    expect(() => serializeApproachSet("vel_kp", NaN)).toThrow(/숫자/);
    expect(() => serializeApproachSet("max_speed", 50)).toThrow(/범위/);
    expect(() => serializeApproachSet("vel_kp", "1\nkill")).toThrow(/숫자/);
  });

  it("caps max_speed at the 0.10 m/s requirement", () => {
    expect(APPROACH_PARAMS.max_speed.max).toBe(10);
    expect(serializeApproachSet("max_speed", 10)).toBe("approach set max_speed 10\n");
    expect(() => serializeApproachSet("max_speed", 10.5)).toThrow(/범위/);
  });
});

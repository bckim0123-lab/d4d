/**
 * AprilTag 저속 접근 제어 시뮬레이터 (순수 함수, DOM 없음).
 *
 * 1) `ApproachController` — 펌웨어 `tag-board-fw/src/approach.h`를 그대로 옮긴 것. 같은 입력이면 같은 출력.
 * 2) `Plant` — 1차 지연이 있는 2D 기체 모델. 스틱 µs → 가속도(cm/s²). 센서(태그·광류) 노이즈와 가림을 흉내 낸다.
 * 3) `DEMO_SCENARIOS` — 심사용 시연 각본. 시간에 따라 태그 가림·수동 전환·광류 저하·바람을 주입한다.
 *
 * 이 파일의 숫자는 실기기 값이 아니다. 스틱→가속도 변환(1µs ≈ 1cm/s²)은 ANGLE 모드 가정의 후보값이다.
 */
import type { ApproachState } from "./hardware";

export interface SimParams {
  maxSpeedCmS: number;
  slowRadiusCm: number;
  stopRadiusCm: number;
  maxAccelCmS2: number;
  velKpUsPerCmS: number;
  velKiUsPerCm: number;
  velIMaxUs: number;
  cmdMaxUs: number;
  cmdSlewUsPerS: number;
  rollSign: number;
  pitchSign: number;
  arriveTolCm: number;
  arriveVelCmS: number;
  arriveHoldMs: number;
  tagStaleMs: number;
  flowStaleMs: number;
  flowMinQuality: number;
  heightStaleMs: number;
  heightMinCm: number;
  heightMaxCm: number;
  maxVelCmS: number;
  deadbandNeutral: number;
  lostNeutral: number;
  overspeedCmS: number;
  handoverSlewUsPerS: number;
}

/** 펌웨어 `ApproachParams` 기본값과 동일 */
export function defaultSimParams(): SimParams {
  return {
    maxSpeedCmS: 10, slowRadiusCm: 25, stopRadiusCm: 5, maxAccelCmS2: 10,
    velKpUsPerCmS: 2, velKiUsPerCm: 0, velIMaxUs: 20, cmdMaxUs: 80, cmdSlewUsPerS: 100,
    rollSign: 1, pitchSign: 1,
    arriveTolCm: 5, arriveVelCmS: 3, arriveHoldMs: 2000,
    tagStaleMs: 600, flowStaleMs: 300, flowMinQuality: 40, heightStaleMs: 300,
    heightMinCm: 20, heightMaxCm: 150, maxVelCmS: 60,
    deadbandNeutral: 1, lostNeutral: 1, overspeedCmS: 12, handoverSlewUsPerS: 100,
  };
}

export interface ControllerInput {
  nowMs: number;
  enabled: boolean;
  authorityAuto: boolean;
  kill: boolean;
  tagValid: boolean;
  tagAtMs: number;
  errRightCm: number;
  errFwdCm: number;
  velValid: boolean;
  velAtMs: number;
  velRightCmS: number;
  velFwdCmS: number;
  flowQuality: number;
  heightValid: boolean;
  heightAtMs: number;
  heightCm: number;
  maskKnown: boolean;
  maskOk: boolean;
}

export interface ControllerOutput {
  state: ApproachState;
  fault: string | null;
  distCm: number;
  targetSpeedCmS: number;
  vTargetRightCmS: number;
  vTargetFwdCmS: number;
  rollUs: number;
  pitchUs: number;
  arriveHeldMs: number;
  neutral: boolean;
  overspeed: boolean;
}

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

export class ApproachController {
  p: SimParams;
  private vtR = 0; private vtF = 0;
  private cmdR = 0; private cmdF = 0;
  private iR = 0; private iF = 0;
  private heldMs = 0;
  private arrived = false;

  constructor(p: SimParams = defaultSimParams()) { this.p = p; }

  /** 거리 → 목표 속도. slow 이상 max, stop 이하 0, 사이는 거리 비례 (펌웨어와 동일) */
  targetSpeed(distCm: number): number {
    const p = this.p;
    if (distCm >= p.slowRadiusCm) return p.maxSpeedCmS;
    if (distCm <= p.stopRadiusCm) return 0;
    if (p.slowRadiusCm <= 1e-3) return 0;
    return p.maxSpeedCmS * distCm / p.slowRadiusCm;
  }

  reset(): void {
    this.vtR = this.vtF = 0;
    this.cmdR = this.cmdF = 0;
    this.iR = this.iF = 0;
    this.heldMs = 0;
    this.arrived = false;
  }

  step(input: ControllerInput, dtSIn: number): ControllerOutput {
    const p = this.p;
    let dtS = dtSIn;
    if (dtS <= 0) dtS = 0.05;
    if (dtS > 0.5) dtS = 0.5;

    const tagFresh = input.tagValid && input.nowMs - input.tagAtMs <= p.tagStaleMs;
    const heightOk = input.heightValid && input.nowMs - input.heightAtMs <= p.heightStaleMs
      && input.heightCm >= p.heightMinCm && input.heightCm <= p.heightMaxCm;
    const velFresh = input.velValid && input.nowMs - input.velAtMs <= p.flowStaleMs
      && input.flowQuality >= p.flowMinQuality;
    const vMag = Math.hypot(input.velRightCmS, input.velFwdCmS);

    let fault: string | null = null;
    if (!input.enabled) fault = "disabled";
    else if (!input.authorityAuto) fault = "manual_authority";
    else if (input.kill) fault = "kill";
    else if (!input.maskKnown) fault = "mask_unknown";
    else if (!input.maskOk) fault = "mask";
    else if (!heightOk) fault = "height";
    else if (!velFresh) fault = "flow";
    else if (vMag > p.maxVelCmS) fault = "velocity";

    if (fault) {
      this.vtR = this.vtF = 0;
      this.iR = this.iF = 0;
      this.heldMs = 0;
      this.arrived = false;
      this.cmdR = this.slewTo(this.cmdR, 0, dtS);
      this.cmdF = this.slewTo(this.cmdF, 0, dtS);
      return this.fill(input.enabled ? "FAULT" : "IDLE", fault, 0, 0);
    }

    let desR = 0; let desF = 0;
    let state: ApproachState;
    let reason: string | null = null;
    let dist = 0; let targetSpeed = 0;
    if (tagFresh) {
      dist = Math.hypot(input.errRightCm, input.errFwdCm);
      targetSpeed = this.targetSpeed(dist);
      if (dist > 1e-3) {
        desR = input.errRightCm / dist * targetSpeed;
        desF = input.errFwdCm / dist * targetSpeed;
      }
      if (dist <= p.arriveTolCm && vMag <= p.arriveVelCmS) {
        this.heldMs += Math.trunc(dtS * 1000);
        if (this.heldMs >= p.arriveHoldMs) this.arrived = true;
      } else {
        this.heldMs = 0;
        if (dist > p.arriveTolCm) this.arrived = false;
      }
      if (this.arrived) {
        desR = desF = 0;
        state = "ARRIVED";
      } else {
        state = dist < p.slowRadiusCm ? "ARRIVING" : "APPROACH";
      }
    } else {
      state = "HOLD";
      reason = "tag_lost";
      this.heldMs = 0;
      if (p.lostNeutral !== 0) {
        this.neutralStep(dtS);
        return this.fill(state, reason, dist, targetSpeed, true);
      }
    }

    // -25: 중립 반환은 과속 제동보다 우선한다. FAULT의 neutral 플래그는 원본처럼 false다.
    if (tagFresh && p.deadbandNeutral !== 0 && (this.arrived || dist <= p.stopRadiusCm)) {
      this.neutralStep(dtS);
      return this.fill(state, reason, dist, targetSpeed, true);
    }
    const overspeed = vMag > p.overspeedCmS;
    if (overspeed) desR = desF = 0;

    let dR = desR - this.vtR; let dF = desF - this.vtF;
    const dMag = Math.hypot(dR, dF);
    const dMax = p.maxAccelCmS2 * dtS;
    if (dMag > dMax && dMag > 1e-6) { dR *= dMax / dMag; dF *= dMax / dMag; }
    this.vtR += dR; this.vtF += dF;

    const eR = this.vtR - input.velRightCmS; const eF = this.vtF - input.velFwdCmS;
    if (p.velKiUsPerCm > 0) {
      this.iR = clamp(this.iR + p.velKiUsPerCm * eR * dtS, -p.velIMaxUs, p.velIMaxUs);
      this.iF = clamp(this.iF + p.velKiUsPerCm * eF * dtS, -p.velIMaxUs, p.velIMaxUs);
    } else {
      this.iR = this.iF = 0;
    }
    const rawR = clamp(p.velKpUsPerCmS * eR + this.iR, -p.cmdMaxUs, p.cmdMaxUs);
    const rawF = clamp(p.velKpUsPerCmS * eF + this.iF, -p.cmdMaxUs, p.cmdMaxUs);
    this.cmdR = this.slewTo(this.cmdR, rawR, dtS);
    this.cmdF = this.slewTo(this.cmdF, rawF, dtS);
    return this.fill(state, reason, dist, targetSpeed, false, overspeed);
  }

  private neutralStep(dtS: number): void {
    this.vtR = this.vtF = 0;
    this.iR = this.iF = 0;
    this.cmdR = this.slewTo(this.cmdR, 0, dtS);
    this.cmdF = this.slewTo(this.cmdF, 0, dtS);
  }

  private slewTo(cmd: number, target: number, dtS: number): number {
    const maxStep = this.p.cmdSlewUsPerS * dtS;
    const d = clamp(target - cmd, -maxStep, maxStep);
    return clamp(cmd + d, -this.p.cmdMaxUs, this.p.cmdMaxUs);
  }

  private fill(state: ApproachState, fault: string | null, distCm: number, targetSpeedCmS: number, neutral = false, overspeed = false): ControllerOutput {
    return {
      state, fault, distCm, targetSpeedCmS,
      vTargetRightCmS: this.vtR, vTargetFwdCmS: this.vtF,
      rollUs: this.p.rollSign * this.cmdR, pitchUs: this.p.pitchSign * this.cmdF,
      arriveHeldMs: this.heldMs,
      neutral, overspeed,
    };
  }
}

// ---------------------------------------------------------------------------
// 기체·센서 모델
// ---------------------------------------------------------------------------

export interface PlantParams {
  /** 스틱 1µs 가 만드는 정상 상태 가속도 (cm/s²). ANGLE 모드 후보값: 10µs ≈ 0.6° ≈ 0.1m/s² → 1 */
  usToAccel: number;
  /** 자세 응답 1차 지연 (s) */
  tauS: number;
  /** 수평 속도 감쇠 (1/s). 공기 저항·호버 복원 근사 */
  drag: number;
  /** 태그 위치 측정 노이즈 (cm, 표준편차) */
  tagNoiseCm: number;
  /** 광류 속도 노이즈 (cm/s, 표준편차) */
  flowNoiseCmS: number;
  /** 카메라 반시야각 (°). 55° HFOV → 27.5 */
  halfFovDeg: number;
  /** 카메라 프레임 간격 (ms). 실측 3~4fps */
  cameraPeriodMs: number;
  heightCm: number;
}

/**
 * 높이 120cm 기본. 55° HFOV 카메라의 지면 발자국 반경은 h·tan(27.5°) 라서 50cm 높이에서는 26cm 밖의 태그가
 * 아예 보이지 않는다. 접근을 시작하려면 태그가 시야 안에 있어야 하므로 시연은 120cm(반경 62cm)에서 한다.
 * 실기 지상 시험에서도 같은 제약이 적용된다 (docs/plans/2026-10-10-approach-control.md).
 */
export function defaultPlantParams(): PlantParams {
  return { usToAccel: 1, tauS: 0.3, drag: 0.15, tagNoiseCm: 0.6, flowNoiseCmS: 0.8, halfFovDeg: 27.5, cameraPeriodMs: 300, heightCm: 120 };
}

/** 결정적 의사난수 (시연이 매번 같게 보이도록) */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rand: () => number): number {
  const u = Math.max(rand(), 1e-9); const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export type Rpy = [number, number, number];

/**
 * 문서 기반 핸드오버 시각 모델. approach.h 바깥의 main.cpp 동작을 설명하며,
 * 실제 50Hz 송신 타이밍·FC 적용 지연을 재현하거나 검증하지 않는다.
 */
export class HandoverModel {
  private base: Rpy = [1500, 1500, 1500];
  private wasAuto = true;

  reset(): void { this.base = [1500, 1500, 1500]; this.wasAuto = true; }

  step(auto: boolean, pilot: Rpy, dtS: number, slewUsPerS: number): Rpy {
    if (!auto) this.base = [...pilot];
    else if (this.wasAuto) {
      const step = Math.max(0, slewUsPerS * dtS);
      this.base = this.base.map((v) => v + clamp(1500 - v, -step, step)) as Rpy;
    }
    // 첫 AUTO 틱은 마지막 수동 기준점을 보존한다. 다음 틱부터 중립으로 수렴.
    this.wasAuto = auto;
    return [...this.base];
  }
}

/** 주입 가능한 외부 조건 */
export interface Disturbance {
  tagOccluded: boolean;     // 태그 가림 (사람이 손으로 가린 상황)
  authorityAuto: boolean;   // CH6 AUTO
  kill: boolean;
  flowQuality: number;      // 0~255. 40 미만이면 제어기가 중지
  windRightCmS2: number;    // 바람 가속도 (cm/s²)
  windFwdCmS2: number;
  maskKnown: boolean;
  maskOk: boolean;
  pilotRpy: Rpy;
}

export function defaultDisturbance(): Disturbance {
  return { tagOccluded: false, authorityAuto: true, kill: false, flowQuality: 58, windRightCmS2: 0, windFwdCmS2: 0, maskKnown: true, maskOk: true, pilotRpy: [1500, 1500, 1500] };
}

/** 한 틱의 기록 (그리기·차트용) */
export interface SimFrame {
  tMs: number;
  /** 기체 위치 (cm, 태그 원점, +x 오른쪽 +y 앞) */
  x: number; y: number;
  vx: number; vy: number;
  /** 제어기가 본 입력 */
  tagVisible: boolean;
  measErrRight: number; measErrFwd: number;
  measVelRight: number; measVelFwd: number;
  out: ControllerOutput;
  baseRpy: Rpy;
  outRpy: Rpy;
  disturbance: Disturbance;
}

export class ApproachSimulation {
  readonly ctrl: ApproachController;
  private handover = new HandoverModel();
  plant: PlantParams;
  disturbance: Disturbance;
  frames: SimFrame[] = [];
  tMs = 0;
  // 기체 상태 (태그 원점)
  x = 0; y = 0; vx = 0; vy = 0;
  ax = 0; ay = 0; // 지연 후 실제 가속도
  private rand: () => number;
  private lastTagAtMs = -1e9;
  private lastTagErrR = 0; private lastTagErrF = 0;
  private lastTagValid = false;

  constructor(startXcm: number, startYcm: number, params = defaultSimParams(), plant = defaultPlantParams(), seed = 7) {
    this.ctrl = new ApproachController(params);
    this.plant = plant;
    this.disturbance = defaultDisturbance();
    this.rand = mulberry32(seed);
    this.x = startXcm; this.y = startYcm;
  }

  reset(startXcm: number, startYcm: number, seed = 7): void {
    this.ctrl.reset();
    this.handover.reset();
    this.frames = [];
    this.tMs = 0;
    this.x = startXcm; this.y = startYcm;
    this.vx = this.vy = this.ax = this.ay = 0;
    this.rand = mulberry32(seed);
    this.lastTagAtMs = -1e9;
    this.lastTagValid = false;
    this.disturbance = defaultDisturbance();
  }

  /** 태그가 카메라 시야 안에 있는가 (지면 발자국 원 안) */
  tagInView(): boolean {
    const r = this.plant.heightCm * Math.tan(this.plant.halfFovDeg * Math.PI / 180);
    return Math.hypot(this.x, this.y) <= r;
  }

  fovRadiusCm(): number {
    return this.plant.heightCm * Math.tan(this.plant.halfFovDeg * Math.PI / 180);
  }

  /** dtS 만큼 진행 (제어기는 20Hz, 카메라는 cameraPeriodMs 마다 새 측정) */
  step(dtS: number): SimFrame {
    const d = this.disturbance;
    const pl = this.plant;
    this.tMs += Math.round(dtS * 1000);

    // 카메라: 주기마다 태그 측정 갱신. 가리거나 시야 밖이면 측정 없음(stale 로 흘러감)
    const visible = !d.tagOccluded && this.tagInView();
    if (visible && this.tMs - this.lastTagAtMs >= pl.cameraPeriodMs) {
      this.lastTagAtMs = this.tMs;
      // 목표(태그)의 기체 기준 위치 = -(기체 위치)
      this.lastTagErrR = -this.x + gauss(this.rand) * pl.tagNoiseCm;
      this.lastTagErrF = -this.y + gauss(this.rand) * pl.tagNoiseCm;
      this.lastTagValid = true;
    }

    const measVelR = this.vx + gauss(this.rand) * pl.flowNoiseCmS;
    const measVelF = this.vy + gauss(this.rand) * pl.flowNoiseCmS;

    const input: ControllerInput = {
      nowMs: this.tMs,
      enabled: true,
      authorityAuto: d.authorityAuto,
      kill: d.kill,
      tagValid: this.lastTagValid,
      tagAtMs: this.lastTagAtMs,
      errRightCm: this.lastTagErrR,
      errFwdCm: this.lastTagErrF,
      velValid: true,
      velAtMs: this.tMs,
      velRightCmS: measVelR,
      velFwdCmS: measVelF,
      flowQuality: d.flowQuality,
      heightValid: true,
      heightAtMs: this.tMs,
      heightCm: pl.heightCm,
      maskKnown: d.maskKnown,
      maskOk: d.maskOk,
    };
    const out = this.ctrl.step(input, dtS);
    const baseRpy = this.handover.step(d.authorityAuto, d.pilotRpy, dtS, this.ctrl.p.handoverSlewUsPerS);
    const outRpy: Rpy = d.authorityAuto
      ? [baseRpy[0] + out.rollUs, baseRpy[1] + out.pitchUs, baseRpy[2]]
      : [...d.pilotRpy];

    // 기체: 명령(µs) → 목표 가속도 → 1차 지연 → 속도·위치. 수동이면 조종기 스틱만 적용.
    // 모델의 "참" 부호는 +롤 = 오른쪽, +피치 = 앞. roll_sign/pitch_sign 을 -1 로 두면 실제로 반대로 날아가는 것이 보인다.
    const cmdR = d.kill ? 0 : outRpy[0] - 1500;
    const cmdF = d.kill ? 0 : outRpy[1] - 1500;
    const targetAx = cmdR * pl.usToAccel;
    const targetAy = cmdF * pl.usToAccel;
    const alpha = dtS / Math.max(pl.tauS, 1e-3);
    this.ax += (targetAx - this.ax) * Math.min(1, alpha);
    this.ay += (targetAy - this.ay) * Math.min(1, alpha);
    this.vx += (this.ax + d.windRightCmS2 - pl.drag * this.vx) * dtS;
    this.vy += (this.ay + d.windFwdCmS2 - pl.drag * this.vy) * dtS;
    this.x += this.vx * dtS;
    this.y += this.vy * dtS;

    const frame: SimFrame = {
      tMs: this.tMs, x: this.x, y: this.y, vx: this.vx, vy: this.vy,
      tagVisible: visible,
      measErrRight: this.lastTagErrR, measErrFwd: this.lastTagErrF,
      measVelRight: measVelR, measVelFwd: measVelF,
      out, baseRpy, outRpy, disturbance: { ...d, pilotRpy: [...d.pilotRpy] },
    };
    this.frames.push(frame);
    if (this.frames.length > 4000) this.frames.splice(0, this.frames.length - 4000);
    return frame;
  }
}

// ---------------------------------------------------------------------------
// 시연 각본
// ---------------------------------------------------------------------------

export type DemoAction =
  | { kind: "caption"; text: string }
  | { kind: "occlude"; on: boolean }
  | { kind: "authority"; auto: boolean }
  | { kind: "flow"; quality: number }
  | { kind: "wind"; right: number; fwd: number }
  | { kind: "kill"; on: boolean }
  | { kind: "pilot"; rpy: Rpy };

export interface DemoCue { atMs: number; actions: DemoAction[] }

export interface DemoScenario {
  id: string;
  title: string;
  summary: string;
  startXcm: number;
  startYcm: number;
  durationMs: number;
  cues: DemoCue[];
  /** 기본 파라미터와 다르게 둘 값 (예: 바람 각본의 속도 I 게인) */
  params?: Partial<SimParams>;
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: "nominal",
    title: "정상 접근",
    summary: "시야 안 55cm 거리의 태그로 10cm/s 이하 저속 접근, 25cm부터 거리 비례 감속, 5cm 안에서 정지·도착 판정",
    startXcm: 35, startYcm: -42, durationMs: 22_000,
    cues: [
      { atMs: 0, actions: [{ kind: "caption", text: "CH6 AUTO · 태그 포착 · 접근 시작 (목표 상한 10cm/s)" }] },
      { atMs: 3_000, actions: [{ kind: "caption", text: "가속도 0.10m/s² 제한으로 부드럽게 순항" }] },
      { atMs: 6_500, actions: [{ kind: "caption", text: "25cm 진입 → 거리 비례 감속" }] },
      { atMs: 11_000, actions: [{ kind: "caption", text: "5cm 안 · 속도 3cm/s 이하 2초 유지 → 도착" }] },
    ],
  },
  {
    id: "tag-lost",
    title: "태그 상실 → 제동 → 복귀",
    summary: "lost_neutral=0 비교 각본: 태그가 600ms 넘게 안 보이면 광류로 제동(HOLD), 재포착 후 접근 재개. -25 기본값 1은 중립 복귀",
    startXcm: -40, startYcm: -35, durationMs: 26_000,
    params: { lostNeutral: 0 },
    cues: [
      { atMs: 0, actions: [{ kind: "caption", text: "접근 중" }] },
      { atMs: 3_000, actions: [{ kind: "occlude", on: true }, { kind: "caption", text: "태그 가림 → HOLD · 광류로 제동" }] },
      { atMs: 8_000, actions: [{ kind: "occlude", on: false }, { kind: "caption", text: "태그 재포착 → 접근 재개" }] },
      { atMs: 15_000, actions: [{ kind: "caption", text: "감속 · 도착 판정" }] },
    ],
  },
  {
    id: "manual",
    title: "수동 전환 회수",
    summary: "CH6 수동에서 FC는 즉시 조종기 입력을 따른다. 접근 제어기 오프셋은 변화율 제한으로 0에 수렴하고 AUTO 복귀 시 재접근",
    startXcm: 45, startYcm: 25, durationMs: 30_000,
    cues: [
      { atMs: 0, actions: [{ kind: "caption", text: "자동 접근 중" }] },
      { atMs: 3_000, actions: [{ kind: "authority", auto: false }, { kind: "caption", text: "CH6 수동 · FC는 조종기 적용 · 오프셋은 중립 수렴" }] },
      { atMs: 8_000, actions: [{ kind: "authority", auto: true }, { kind: "caption", text: "CH6 → 자동(1000) · 적분 초기화 후 재접근" }] },
      { atMs: 16_000, actions: [{ kind: "caption", text: "도착 판정" }] },
    ],
  },
  {
    id: "flow-degraded",
    title: "광류 신뢰도 저하 중지",
    summary: "광류 품질이 임계(40) 아래로 떨어지면 속도를 믿을 수 없으므로 접근을 중단한다",
    startXcm: -30, startYcm: 42, durationMs: 26_000,
    cues: [
      { atMs: 0, actions: [{ kind: "caption", text: "광류 q=58 · 접근 중" }] },
      { atMs: 3_000, actions: [{ kind: "flow", quality: 22 }, { kind: "caption", text: "광류 q=22 < 40 → FAULT flow · 명령 0" }] },
      { atMs: 9_000, actions: [{ kind: "flow", quality: 55 }, { kind: "caption", text: "품질 회복 → 재접근" }] },
    ],
  },
  {
    id: "handover",
    title: "수동 → AUTO 기준점 핸드오버",
    summary: "문서 기반 시각 모델: 수동 R/P/Y 기준점을 AUTO 진입에서 고정하고 1500µs로 수렴. 이 각본만 handover_slew=10µs/s(기본 100). 목표 속도는 10cm/s 이하이나 수동 스틱·기준점 잔류로 실제 기체 속도는 초과할 수 있음",
    startXcm: -15, startYcm: 20, durationMs: 22_000,
    params: { handoverSlewUsPerS: 10 },
    cues: [
      { atMs: 0, actions: [{ kind: "authority", auto: false }, { kind: "pilot", rpy: [1520, 1485, 1510] }, { kind: "caption", text: "수동 · 기준점이 조종기 R/P/Y 1520/1485/1510을 추적" }] },
      { atMs: 1_000, actions: [{ kind: "authority", auto: true }, { kind: "caption", text: "AUTO · 마지막 수동 기준점 고정 → 1500으로 수렴" }] },
      { atMs: 1_500, actions: [{ kind: "pilot", rpy: [1480, 1520, 1490] }, { kind: "caption", text: "AUTO 중 스틱을 움직여도 기준점은 다시 추적하지 않음" }] },
      { atMs: 3_500, actions: [{ kind: "caption", text: "기준점 R/P/Y = 1500 · 롤/피치 제어 오프셋만 합산" }] },
    ],
  },
  {
    id: "wind",
    title: "바람 외란 속에서 도착 (속도 PI)",
    summary: "deadband_neutral=0 비교 각본: 속도 PI(vel_ki=1)로 중앙에서도 바람을 상쇄한다. -25 기본값 1의 중립 복귀와 구분한다",
    startXcm: 20, startYcm: -50, durationMs: 28_000,
    params: { velKiUsPerCm: 1.0, velIMaxUs: 20, deadbandNeutral: 0 },
    cues: [
      { atMs: 0, actions: [{ kind: "wind", right: 4, fwd: 0 }, { kind: "caption", text: "옆바람 4cm/s² · vel_ki=1 로 접근" }] },
      { atMs: 8_000, actions: [{ kind: "wind", right: -6, fwd: 2 }, { kind: "caption", text: "돌풍 방향 전환 → 적분항이 다시 바람을 흡수" }] },
      { atMs: 16_000, actions: [{ kind: "caption", text: "도착 판정 · 바람에 밀리면 재접근" }] },
    ],
  },
];

/** 각본에 맞는 파라미터 (기본값 + 각본 덮어쓰기) */
export function scenarioParams(scenario: DemoScenario, base: SimParams = defaultSimParams()): SimParams {
  return { ...base, ...(scenario.params ?? {}) };
}

/** 각본 적용기: 지난 큐를 순서대로 적용하고 현재 자막을 돌려준다 */
export class DemoScript {
  private nextCue = 0;
  caption = "";
  constructor(readonly scenario: DemoScenario) {}

  reset(): void { this.nextCue = 0; this.caption = ""; }

  apply(sim: ApproachSimulation): void {
    const cues = this.scenario.cues;
    while (this.nextCue < cues.length && cues[this.nextCue].atMs <= sim.tMs) {
      for (const action of cues[this.nextCue].actions) {
        switch (action.kind) {
          case "caption": this.caption = action.text; break;
          case "occlude": sim.disturbance.tagOccluded = action.on; break;
          case "authority": sim.disturbance.authorityAuto = action.auto; break;
          case "flow": sim.disturbance.flowQuality = action.quality; break;
          case "wind": sim.disturbance.windRightCmS2 = action.right; sim.disturbance.windFwdCmS2 = action.fwd; break;
          case "kill": sim.disturbance.kill = action.on; break;
          case "pilot": sim.disturbance.pilotRpy = [...action.rpy]; break;
        }
      }
      this.nextCue++;
    }
  }

  finished(sim: ApproachSimulation): boolean { return sim.tMs >= this.scenario.durationMs; }
}

/** 시연 요약 통계 (심사용 결과 카드) */
export interface SimSummary {
  arrived: boolean;
  arrivedAtMs: number | null;
  maxSpeedCmS: number;
  finalDistCm: number;
  faultCount: number;
  holdCount: number;
  maxCmdUs: number;
}

export function summarize(frames: SimFrame[]): SimSummary {
  let arrivedAtMs: number | null = null;
  let maxSpeed = 0; let maxCmd = 0; let faultCount = 0; let holdCount = 0;
  let prev: ApproachState | null = null;
  for (const f of frames) {
    maxSpeed = Math.max(maxSpeed, Math.hypot(f.vx, f.vy));
    maxCmd = Math.max(maxCmd, Math.abs(f.out.rollUs), Math.abs(f.out.pitchUs));
    if (f.out.state === "ARRIVED" && arrivedAtMs == null) arrivedAtMs = f.tMs;
    if (f.out.state === "FAULT" && prev !== "FAULT") faultCount++;
    if (f.out.state === "HOLD" && prev !== "HOLD") holdCount++;
    prev = f.out.state;
  }
  const last = frames[frames.length - 1];
  return {
    arrived: last?.out.state === "ARRIVED",
    arrivedAtMs,
    maxSpeedCmS: maxSpeed,
    finalDistCm: last ? Math.hypot(last.x, last.y) : 0,
    faultCount, holdCount, maxCmdUs: maxCmd,
  };
}

export const NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
export const NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
export const NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";

export type TelemetryType =
  | "boot"
  | "info"
  | "tags"
  | "flow"
  | "tag"
  | "target"
  | "vision"
  | "msp"
  | "fc"
  | "rc"
  | "mic"
  | "voice"
  | "cmd_error"
  | "help"
  | "motortest"
  | "kill"
  | "gimbal"
  | "approach"
  | "approach_params"
  | "approach_mode"
  | "approach_set"
  | "authority"
  | "warn";

interface TelemetryBase {
  type: TelemetryType;
  receivedAt: number;
  [key: string]: unknown;
}

export type ControlAuthority = "auto" | "manual";

export interface InfoTelemetry extends TelemetryBase {
  type: "boot" | "info";
  build?: string;
  camera_sensor?: string;
  frame?: string;
  msp?: boolean;
  fc_tx?: number;
  fc_rx?: number;
  authority?: ControlAuthority;
  authority_fresh?: boolean;
  ch6?: number | null;
  rc_tx?: boolean;
  kill?: boolean;
  kill_tx?: boolean;
  ble_connected?: boolean;
  axes_verified?: boolean;
  approach?: ApproachMode;
  tx_hz_target?: number;
  tx_always?: boolean;
  rcmap?: string;
  fc_mask?: number | null;
  mask_ok?: boolean | null;
  thr_slot?: number;
}

/** 접근 제어 모드. 펌웨어 `TAGVISION-20261010-25` `approach` 명령과 같다. */
export type ApproachMode = "off" | "shadow" | "on" | "legacy";
export type ApproachState = "IDLE" | "APPROACH" | "ARRIVING" | "ARRIVED" | "HOLD" | "FAULT";

/** 보드 접근 제어기 5Hz 보고. 속도 벡터는 [오른쪽, 앞] cm/s. */
export interface ApproachTelemetry extends TelemetryBase {
  type: "approach";
  mode?: ApproachMode;
  state?: ApproachState;
  fault?: string | null;
  dist_cm?: number;
  err_right_cm?: number;
  err_fwd_cm?: number;
  target_speed?: number;
  v_target?: [number, number];
  v_flow?: [number, number];
  v_flow_valid?: boolean;
  flow_q?: number;
  v_tag?: [number | null, number | null];
  roll_us?: number;
  pitch_us?: number;
  tag?: "fresh" | "stale" | "none";
  authority?: ControlAuthority;
  ch6?: number;
  height_cm?: number;
  height_valid?: boolean;
  att?: [number | null, number | null];
  arrive_held_ms?: number;
  neutral?: boolean;
  overspeed?: boolean;
  vz_cm_s?: number | null;
  vz_warn?: boolean;
  mask_ok?: boolean | null;
  out_rpy?: [number, number, number];
  thr_slot_us?: number;
  rc_tx?: boolean;
}

export interface ApproachParamsTelemetry extends TelemetryBase {
  type: "approach_params";
  mode?: ApproachMode;
  params?: Record<string, number>;
}

export interface ApproachModeTelemetry extends TelemetryBase {
  type: "approach_mode";
  mode?: ApproachMode;
  reset?: boolean;
  fc_mask?: number | null;
  warning?: string | null;
}

/** CH6 전환 시 한 번 보고. 지속 상태 판정에는 주기적인 msp 보고를 쓴다. */
export interface AuthorityTelemetry extends TelemetryBase {
  type: "authority";
  to?: ControlAuthority;
  ch6?: number;
  t?: number;
  frame_age_ms?: number;
  tx_hz?: number;
  out_rpy?: [number, number, number];
  thr_slot_us?: number;
  pilot_rpy?: [number, number, number];
  pilot_valid?: boolean;
  fc_rc?: number[];
  approach?: ApproachMode;
}

export interface WarningTelemetry extends TelemetryBase {
  type: "warn";
  code?: "vz";
  vz_cm_s?: number | null;
  limit_cm_s?: number;
  height_cm?: number;
  height_src?: string;
  throttle_action?: "none";
}

export interface ApproachSetTelemetry extends TelemetryBase {
  type: "approach_set";
  name?: string;
  value?: number;
}

export interface FlowTelemetry extends TelemetryBase {
  type: "flow";
  connected?: boolean;
  height_cm?: number | null;
  forward_cm_s?: number | null;
  right_cm_s?: number | null;
  up_cm_s?: number | null;
  quality?: number;
  packets?: number;
  errors?: number;
  proto?: string;
  height_src?: string;
}

export interface VisionTelemetry extends TelemetryBase {
  type: "vision";
  fps?: number;
  detect_ms?: number;
  ids?: number[];
  target_id?: number;
  phase?: string;
}

export interface MspTelemetry extends TelemetryBase {
  type: "msp";
  enabled?: boolean;
  fc_link?: boolean;
  sent?: number;
  acks?: number;
  rc_replies?: number;
  errors?: number;
  out?: number[];
  fc_rc?: number[];
  authority?: ControlAuthority;
  authority_fresh?: boolean;
  ch5?: number | null;
  ch6?: number | null;
  rc_throttle?: number | null;
  rc_tx?: boolean;
  kill?: boolean;
  kill_tx?: boolean;
  phase?: string;
  tx_hz?: number;
  tx_always?: boolean;
  rcmap?: string;
  rcmap_src?: "fc" | "default";
  fc_mask?: number | null;
  mask_ok?: boolean | null;
  mask_recommended?: number;
  thr_slot?: number;
  thr_slot_us?: number;
  base_rpy?: [number, number, number];
  pilot_rpy?: [number, number, number];
  pilot_valid?: boolean;
  auto_since_ms?: number;
  vz_cm_s?: number | null;
  vz_warn?: boolean;
  vz_warn_count?: number;
}

/** XIAO가 UART5로 읽은 FC 상태. 필드 이름은 펌웨어 `TAGVISION-20261010-22`의 `fc` 줄과 같다. */
export interface FcBoardTelemetry extends TelemetryBase {
  type: "fc";
  status?: boolean;
  armed?: boolean;
  arming_flags?: string;
  blockers?: string[];
  battery?: boolean;
  vbat?: number;
  cells?: number;
  amps?: number;
  motor?: number[];
  motor_ok?: boolean;
  kill?: boolean;
}

export interface MotorTestTelemetry extends TelemetryBase {
  type: "motortest";
  state?: "running" | "stopping" | "stopped";
  reason?: string;
  motors?: string;
  value?: number;
  ms?: number;
  fc_acks?: number;
}

export interface KillTelemetry extends TelemetryBase {
  type: "kill";
  state?: "on" | "off";
}

export interface MicTelemetry extends TelemetryBase {
  type: "mic";
  ok?: boolean;
  rms_db?: number;
  peak_db?: number;
  recording?: boolean;
}

export interface GenericTelemetry extends TelemetryBase {
  type:
    | "tags"
    | "tag"
    | "target"
    | "rc"
    | "voice"
    | "cmd_error"
    | "help"
    | "gimbal";
  reason?: string;
}

export type TelemetryMessage =
  | InfoTelemetry
  | FlowTelemetry
  | VisionTelemetry
  | MspTelemetry
  | FcBoardTelemetry
  | MicTelemetry
  | MotorTestTelemetry
  | KillTelemetry
  | ApproachTelemetry
  | ApproachParamsTelemetry
  | ApproachModeTelemetry
  | ApproachSetTelemetry
  | AuthorityTelemetry
  | WarningTelemetry
  | GenericTelemetry;

export type TelemetryLatest = {
  boot?: InfoTelemetry;
  info?: InfoTelemetry;
  tags?: GenericTelemetry;
  flow?: FlowTelemetry;
  tag?: GenericTelemetry;
  target?: GenericTelemetry;
  vision?: VisionTelemetry;
  msp?: MspTelemetry;
  fc?: FcBoardTelemetry;
  rc?: GenericTelemetry;
  mic?: MicTelemetry;
  voice?: GenericTelemetry;
  cmd_error?: GenericTelemetry;
  help?: GenericTelemetry;
  motortest?: MotorTestTelemetry;
  kill?: KillTelemetry;
  gimbal?: GenericTelemetry;
  approach?: ApproachTelemetry;
  approach_params?: ApproachParamsTelemetry;
  approach_mode?: ApproachModeTelemetry;
  approach_set?: ApproachSetTelemetry;
  authority?: AuthorityTelemetry;
  warn?: WarningTelemetry;
};

export interface TelemetryParseError {
  raw: string;
  message: string;
  receivedAt: number;
}

export interface TelemetryState {
  buffer: string;
  latest: TelemetryLatest;
  errors: TelemetryParseError[];
  lastReceivedAt?: number;
}

const TELEMETRY_TYPES: ReadonlySet<string> = new Set<TelemetryType>([
  "boot",
  "info",
  "tags",
  "flow",
  "tag",
  "target",
  "vision",
  "msp",
  "fc",
  "rc",
  "mic",
  "voice",
  "cmd_error",
  "help",
  "motortest",
  "kill",
  "gimbal",
  "approach",
  "approach_params",
  "approach_mode",
  "approach_set",
  "authority",
  "warn",
]);

export function createTelemetryState(): TelemetryState {
  return { buffer: "", latest: {}, errors: [] };
}

function parseTelemetryLine(
  line: string,
  receivedAt: number,
): TelemetryMessage {
  const value: unknown = JSON.parse(line);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("JSON 객체가 아닙니다.");
  }
  const record = value as Record<string, unknown>;
  if (typeof record.type !== "string" || !TELEMETRY_TYPES.has(record.type)) {
    throw new Error("알 수 없는 텔레메트리 type입니다.");
  }
  return { ...record, receivedAt } as TelemetryMessage;
}

export function consumeTelemetryChunk(
  previous: TelemetryState,
  chunk: string,
  receivedAt = Date.now(),
): TelemetryState {
  const lines = `${previous.buffer}${chunk}`.split(/\r?\n/);
  const buffer = lines.pop() ?? "";
  const latest: TelemetryLatest = { ...previous.latest };
  const errors = [...previous.errors];
  let accepted = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    try {
      const message = parseTelemetryLine(line, receivedAt);
      Object.assign(latest, { [message.type]: message });
      accepted = true;
    } catch (error) {
      errors.push({
        raw: line.slice(0, 240),
        message: error instanceof Error ? error.message : "파싱 오류",
        receivedAt,
      });
    }
  }

  return {
    buffer,
    latest,
    errors: errors.slice(-20),
    lastReceivedAt: accepted ? receivedAt : previous.lastReceivedAt,
  };
}

export type Freshness = "missing" | "fresh" | "stale";

export function freshness(
  receivedAt: number | undefined,
  now = Date.now(),
  staleAfterMs = 3_000,
): Freshness {
  if (receivedAt == null) return "missing";
  return now - receivedAt <= staleAfterMs ? "fresh" : "stale";
}

export interface FcSerialPort {
  port_label: string;
  functions: string[];
  [key: string]: unknown;
}

export interface FcGate {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
  remediation: string;
  required_for_apply: boolean;
  required_for_restore: boolean;
}

export interface FcStatus {
  port: string;
  identity: {
    variant?: string;
    firmware?: { version?: string };
    board?: { board_name?: string; board_id?: string };
  };
  armed: boolean;
  arm_blockers: string[];
  sensors: string[];
  rc: { channel_count?: number; channels_us?: number[] };
  motors_us: number[];
  modes: Array<{
    permanent_id?: number;
    mode?: string;
    aux_channel?: number;
    range_us?: number[];
  }>;
  serial_ports: FcSerialPort[];
  mask: number | null;
  min_check: number | null;
  throttle_us: number | null;
  can_apply_67: boolean;
  can_apply_override?: boolean;
  can_restore_64: boolean;
  gates: FcGate[];
  flight_locked: boolean;
}

export type BoardLinkState = "disconnected" | "stale" | "online";

export function deriveBoardLinkState(
  connected: boolean,
  lastReceivedAt: number | undefined,
  now = Date.now(),
  staleAfterMs = 3_000,
): BoardLinkState {
  if (!connected) return "disconnected";
  return freshness(lastReceivedAt, now, staleAfterMs) === "fresh"
    ? "online"
    : "stale";
}

export interface RcComparison {
  available: boolean;
  board?: { roll: number; pitch: number };
  fc?: { roll: number; pitch: number };
  delta?: { roll: number; pitch: number };
  matches?: boolean;
}

function frameMap(msp: MspTelemetry): string | null {
  if (msp.rcmap !== undefined) {
    return typeof msp.rcmap === "string" && /^[AETR]{4}$/.test(msp.rcmap) && new Set(msp.rcmap).size === 4
      ? msp.rcmap : null;
  }
  // -25 이전 펌웨어만 고정 AETR를 사용했다. 새 인터페이스의 매핑 누락은 추측하지 않는다.
  return msp.tx_always === undefined && msp.tx_hz === undefined && msp.rcmap_src === undefined
    && msp.thr_slot === undefined && msp.thr_slot_us === undefined && msp.fc_mask === undefined && msp.mask_ok === undefined
    ? "AETR" : null;
}

export interface BoardThrottle {
  slot: number | null;
  value: number | null;
  valid: boolean;
}

/** FC 논리 스로틀(CH4)과 MSP 프레임 슬롯을 구별하고 상충하는 보고는 신뢰하지 않는다. */
export function deriveBoardThrottle(msp: MspTelemetry | undefined): BoardThrottle {
  if (!msp) return { slot: null, value: null, valid: false };
  const map = frameMap(msp);
  const reportedSlot = msp.thr_slot;
  const slot = reportedSlot !== undefined
    ? Number.isInteger(reportedSlot) && reportedSlot >= 0 && reportedSlot < 4 ? reportedSlot : null
    : map ? map.indexOf("T") : null;
  const frameValue = slot != null && Array.isArray(msp.out) ? msp.out[slot] : undefined;
  const reportValue = msp.thr_slot_us;
  const value = reportValue !== undefined ? reportValue : frameValue;
  const finite = typeof value === "number" && Number.isFinite(value) && value >= 800 && value <= 2200;
  const consistent = (reportValue === undefined || frameValue === undefined || reportValue === frameValue)
    && (msp.rcmap === undefined || map !== null)
    && (map === null || slot === map.indexOf("T"));
  return { slot, value: finite ? value : null, valid: slot !== null && finite && consistent };
}

export function deriveRcComparison(
  msp: MspTelemetry | undefined,
  toleranceUs = 5,
  now = Date.now(),
): RcComparison {
  if (
    msp?.rc_tx !== true
    || msp.authority !== "auto"
    || msp.authority_fresh !== true
    || freshness(msp.receivedAt, now, 2_000) !== "fresh"
    || !Array.isArray(msp.out)
    || !Array.isArray(msp.fc_rc)
    || msp.out.length < 2
    || msp.fc_rc.length < 2
  ) {
    return { available: false };
  }
  const map = frameMap(msp);
  if (!map) return { available: false };
  const board = { roll: msp.out[map.indexOf("A")], pitch: msp.out[map.indexOf("E")] };
  const fc = { roll: msp.fc_rc[0], pitch: msp.fc_rc[1] };
  if (![board.roll, board.pitch, fc.roll, fc.pitch].every(Number.isFinite)) {
    return { available: false };
  }
  const delta = {
    roll: fc.roll - board.roll,
    pitch: fc.pitch - board.pitch,
  };
  return {
    available: true,
    board,
    fc,
    delta,
    matches:
      Math.abs(delta.roll) <= toleranceUs
      && Math.abs(delta.pitch) <= toleranceUs,
  };
}

export interface ControlPanelState {
  groundReady: boolean;
  actuationEnabled: boolean;
  flightLocked: true;
  canApply67: boolean;
  canApplyOverride: boolean;
  canRestore64: boolean;
  reasons: string[];
}

export function deriveControlPanelState({
  boardLink,
  fc,
  fcFresh,
  msp,
  now = Date.now(),
}: {
  boardLink: BoardLinkState;
  fc: FcStatus | undefined;
  fcFresh: boolean;
  msp: MspTelemetry | undefined;
  now?: number;
}): ControlPanelState {
  const reasons: string[] = [];
  if (boardLink !== "online") {
    reasons.push(
      boardLink === "disconnected"
        ? "XIAO BLE가 연결되지 않았습니다."
        : "XIAO 텔레메트리가 오래됐습니다.",
    );
  }
  if (!fc || !fcFresh) {
    reasons.push("FC 상태가 없거나 오래됐습니다.");
  } else {
    if (fc.armed !== false) reasons.push("FC가 아밍됐거나 상태를 확인할 수 없습니다.");
    if (![7, 64, 67, 71].includes(fc.mask ?? -1)) reasons.push("FC 마스크가 허용된 7/64/67/71이 아닙니다. 스로틀 비트는 허용하지 않습니다.");
    if (
      fc.throttle_us == null
      || fc.min_check == null
      || fc.throttle_us >= fc.min_check
    ) {
      reasons.push("스로틀이 min_check 아래가 아닙니다.");
    }
    if (
      fc.motors_us.length < 4
      || fc.motors_us.slice(0, 4).some((value) => value !== 1000)
    ) {
      reasons.push("M1~M4가 모두 1000µs가 아닙니다.");
    }
    for (const gate of fc.gates) {
      if (gate.required_for_apply && !gate.passed) {
        reasons.push(`${gate.label}: ${gate.detail}`);
      }
    }
  }
  if (msp?.fc_link !== true) reasons.push("XIAO↔FC UART5 MSP 링크가 없습니다.");
  if (msp?.authority_fresh !== true) reasons.push("CH6 제어권 정보가 오래됐습니다.");
  const mspFresh = freshness(msp?.receivedAt, now, 2_000) === "fresh";
  if (!mspFresh) reasons.push("보드 MSP 보고가 없거나 2초 넘게 오래됐습니다.");
  const throttle = deriveBoardThrottle(msp);
  if (msp?.rc_tx !== true && msp?.rc_tx !== false) reasons.push("보드 프레임 송신 여부를 확인할 수 없습니다.");
  if (msp?.rc_tx === true) {
    if (!throttle.valid) reasons.push("보드 스로틀 슬롯 매핑·송신값을 확인할 수 없습니다.");
    if (throttle.value != null && throttle.value >= 1100) {
      reasons.push(`보드가 스로틀 슬롯에 ${throttle.value}µs를 보냅니다. 펌웨어 -24 이상(스로틀 1000 고정)을 올리세요.`);
    }
  }

  const groundReady = reasons.length === 0;
  return {
    groundReady,
    actuationEnabled: groundReady,
    flightLocked: true,
    canApply67: groundReady && fc?.can_apply_67 === true,
    canApplyOverride: groundReady && (fc?.can_apply_override ?? fc?.can_apply_67) === true,
    canRestore64:
      boardLink === "online"
      && fcFresh
      && mspFresh
      && msp?.fc_link === true
      && msp?.authority_fresh === true
      && fc?.armed === false
      && [7, 64, 67, 71].includes(fc.mask ?? -1)
      && fc?.can_restore_64 === true,
    reasons: [...new Set(reasons)],
  };
}

/** UI 표시와 실제 전송 직전에 같은 게이트를 평가한다. 호출 시각을 반드시 새로 읽는다. */
export function deriveBoardCommandAvailability({ connected, telemetry, fc, fcAt, fcQueryFailed = false, now = Date.now() }: {
  connected: boolean;
  telemetry: TelemetryState;
  fc: FcStatus | undefined;
  fcAt: number | undefined;
  fcQueryFailed?: boolean;
  now?: number;
}): { commandsEnabled: boolean; approachOutputEnabled: boolean; killEnabled: boolean; panel: ControlPanelState } {
  const boardLink = deriveBoardLinkState(connected, telemetry.lastReceivedAt, now);
  const fcFresh = !fcQueryFailed && freshness(fcAt, now, 8_000) === "fresh";
  const msp = telemetry.latest.msp;
  const panel = deriveControlPanelState({ boardLink, fc, fcFresh, msp, now });
  const commandsEnabled = boardLink === "online" && freshness(msp?.receivedAt, now, 2_000) === "fresh"
    && fcFresh && fc?.armed === false && msp?.fc_link === true;
  const approachFresh = freshness(telemetry.latest.approach?.receivedAt, now, 2_000) === "fresh";
  return { commandsEnabled, approachOutputEnabled: commandsEnabled && panel.groundReady && approachFresh, killEnabled: connected, panel };
}

export interface MtfHealth {
  state: "healthy" | "degraded" | "unknown";
  source: "board" | "fc" | "none";
  detail: string;
}

export function deriveMtfHealth(
  flow: FlowTelemetry | undefined,
  fc: FcStatus | undefined,
): MtfHealth {
  if (flow?.connected === true) {
    return {
      state: "healthy",
      source: "board",
      detail: `보드 센서 링크 정상${flow.proto ? ` · ${flow.proto}` : ""}`,
    };
  }

  const sensors = fc?.sensors ?? [];
  const rangefinder = sensors.some((name) => name.includes("레인지"));
  const opticalFlow = sensors.some(
    (name) => name.includes("광류") || name.toLowerCase().includes("opflow"),
  );
  const uart4Msp = (fc?.serial_ports ?? []).some(
    (port) =>
      port.port_label === "UART4" && port.functions.includes("MSP"),
  );
  if (rangefinder && opticalFlow && uart4Msp) {
    return {
      state: "healthy",
      source: "fc",
      detail: "FC UART4 MSP에서 거리·광류 감지",
    };
  }
  if (flow?.connected === false || fc) {
    return {
      state: "degraded",
      source: "none",
      detail: "MTF-01P 거리·광류 경로를 확인하세요.",
    };
  }
  return {
    state: "unknown",
    source: "none",
    detail: "MTF-01P 데이터 없음",
  };
}

export type PillTone = "cyan" | "amber" | "red" | "muted";

export interface AuthorityView {
  state: "auto" | "manual" | "unknown";
  label: string;
  detail: string;
  tone: PillTone;
}

/** CH6 제어권. 응답이 오래됐거나 없으면 펌웨어와 같은 fail-closed 규칙으로 unknown 처리한다. */
export function deriveAuthorityView(
  msp: MspTelemetry | undefined,
  now = Date.now(),
): AuthorityView {
  if (!msp || msp.authority == null) {
    return { state: "unknown", label: "--", detail: "제어권 정보 없음", tone: "muted" };
  }
  const ch6 = msp.ch6 != null ? `CH6 ${msp.ch6}` : "CH6 없음";
  if (msp.authority_fresh !== true || freshness(msp.receivedAt, now, 2_000) !== "fresh") {
    return {
      state: "unknown",
      label: "UNKNOWN",
      detail: `${ch6} · 응답 만료 → 보드는 MANUAL로 처리`,
      tone: "red",
    };
  }
  const tx = msp.rc_tx === true ? `프레임 송신${Number.isFinite(msp.tx_hz) ? ` ${msp.tx_hz}Hz` : " 중"}` : msp.rc_tx === false ? "프레임 송신 정지" : "프레임 송신 미확인";
  if (msp.authority === "auto") {
    return {
      state: "auto",
      label: "AUTO",
      detail: `${ch6} · FC 보드 제어권 · ${tx}`,
      tone: "cyan",
    };
  }
  return {
    state: "manual",
    label: "MANUAL",
    detail: `${ch6} · 조종기 제어 · ${tx} · 보드 프레임 FC 미적용`,
    tone: "amber",
  };
}

const HEIGHT_SOURCE_BUILD = 21;

function buildSerial(build: string | undefined): number | null {
  const match = build?.match(/-(\d+)$/);
  return match ? Number(match[1]) : null;
}

/**
 * 비행 출력 잠금 사유. 자동으로 검증할 수 없는 항목(ESC·모터 방향·킬 재시험)은
 * 항상 남겨 두어 UI가 이 단계에서 "비행 가능"을 표시하지 않도록 한다.
 */
export function deriveFlightLockReasons({
  info,
  mtf,
}: {
  info: InfoTelemetry | undefined;
  mtf: MtfHealth | undefined;
}): string[] {
  const reasons: string[] = [];
  if (info?.axes_verified !== true) {
    reasons.push("카메라/기체 축 미검증(axes_verified:false): 태그 이동 방향과 롤·피치 부호를 실측해야 합니다.");
  }
  const serial = buildSerial(info?.build);
  if (serial == null) {
    reasons.push("보드 빌드 정보 없음: info를 요청해 FC 높이 소스를 지원하는 -21 이상인지 확인하세요.");
  } else if (serial < HEIGHT_SOURCE_BUILD) {
    reasons.push(`보드 빌드 ${info?.build}는 FC 높이 소스를 읽지 못합니다. -21 이상을 올리세요.`);
  }
  if (!mtf || mtf.state !== "healthy") {
    reasons.push(`MTF-01P 거리·광류 경로 미확인${mtf?.detail ? `: ${mtf.detail}` : ""}`);
  }
  reasons.push("ESC 응답·모터 순서·회전 방향·공회전·킬 재시험은 사람이 별도로 확인해야 합니다.");
  return reasons;
}

/** FastAPI `{"detail": {"code","message"}}` 또는 문자열 detail에서 사용자 메시지를 뽑는다. */
export function hardwareErrorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) return message;
    }
  }
  // JSON 본문이 없는 5xx는 FastAPI가 아니라 개발 프록시/서버 부재가 원인이다.
  if (status >= 500) return `백엔드 API 응답 없음 (HTTP ${status})`;
  return `HTTP ${status}`;
}

export const OVERRIDE_CONFIRMATION = "프로펠러 제거 / 디스아밍";

export const BOARD_COMMANDS = {
  info: { confirmationRequired: false, label: "상태 요청" },
  tags: { confirmationRequired: false, label: "태그 목록" },
  snap: { confirmationRequired: false, label: "카메라 스냅샷" },
  "msp on": { confirmationRequired: false, label: "MSP 제어 시작" },
  "msp off": { confirmationRequired: false, label: "MSP 제어 중지" },
  "mission reset": { confirmationRequired: false, label: "미션 초기화" },
  kill: { confirmationRequired: false, label: "비상 정지" },
  "kill off": { confirmationRequired: true, label: "비상 정지 해제" },
  // 접근 오프셋은 shadow/off에서 적용되지 않는다. -25 RC 프레임 송신은 모드와 별개다.
  // on/legacy 는 접근 오프셋 적용 모드이므로 확인이 필요하다.
  approach: { confirmationRequired: false, label: "접근 제어 상태 조회" },
  "approach params": { confirmationRequired: false, label: "접근 제어 파라미터 조회" },
  "approach shadow": { confirmationRequired: false, label: "접근 제어 shadow(계산만)" },
  "approach off": { confirmationRequired: false, label: "접근 제어 끔" },
  "approach reset": { confirmationRequired: false, label: "접근 제어 적분·명령 초기화" },
  "approach on": { confirmationRequired: true, label: "접근 제어 출력 켬" },
  "approach legacy": { confirmationRequired: true, label: "기존 4µs/cm 위치 제어" },
} as const;

export type BoardCommand = keyof typeof BOARD_COMMANDS;

export function serializeBoardCommand(
  command: unknown,
  confirmed = false,
): string {
  if (
    typeof command !== "string"
    || !Object.prototype.hasOwnProperty.call(BOARD_COMMANDS, command)
  ) {
    throw new Error("허용되지 않은 보드 명령입니다.");
  }
  const allowed = command as BoardCommand;
  if (BOARD_COMMANDS[allowed].confirmationRequired && !confirmed) {
    throw new Error(`${BOARD_COMMANDS[allowed].label} 확인이 필요합니다.`);
  }
  return `${allowed}\n`;
}

/**
 * 이름은 펌웨어 `approachSetParam()`과 같다. 아래 범위는 지상 시험용 UI의 제한이다.
 * 펌웨어 setter 자체는 범위를 제한하지 않으므로 반드시 여기서 검사한다. 전부 RAM 값이다.
 */
export const APPROACH_PARAMS: Readonly<Record<string, { label: string; unit: string; min: number; max: number; step: number }>> = {
  max_speed: { label: "최대 접근 속도", unit: "cm/s", min: 1, max: 10, step: 1 }, // 요구: 0.10 m/s 상한
  slow_radius: { label: "감속 시작 거리", unit: "cm", min: 5, max: 200, step: 1 },
  stop_radius: { label: "정지 거리", unit: "cm", min: 0, max: 50, step: 1 },
  max_accel: { label: "최대 가속도", unit: "cm/s²", min: 1, max: 100, step: 1 },
  vel_kp: { label: "속도 P 게인", unit: "µs/(cm/s)", min: 0, max: 20, step: 0.1 },
  vel_ki: { label: "속도 I 게인", unit: "µs/cm", min: 0, max: 10, step: 0.1 },
  vel_imax: { label: "적분 상한", unit: "µs", min: 0, max: 80, step: 1 },
  cmd_max: { label: "스틱 명령 상한", unit: "µs", min: 10, max: 100, step: 5 },
  cmd_slew: { label: "명령 변화율 상한", unit: "µs/s", min: 10, max: 1000, step: 10 },
  roll_sign: { label: "롤 부호", unit: "", min: -1, max: 1, step: 2 },
  pitch_sign: { label: "피치 부호", unit: "", min: -1, max: 1, step: 2 },
  arrive_tol: { label: "도착 오차 허용", unit: "cm", min: 1, max: 50, step: 1 },
  arrive_vel: { label: "도착 속도 허용", unit: "cm/s", min: 0.5, max: 20, step: 0.5 },
  arrive_hold_ms: { label: "도착 유지 시간", unit: "ms", min: 100, max: 10000, step: 100 },
  tag_stale_ms: { label: "태그 만료", unit: "ms", min: 100, max: 5000, step: 100 },
  flow_stale_ms: { label: "광류 만료", unit: "ms", min: 50, max: 2000, step: 50 },
  flow_min_q: { label: "광류 최소 품질", unit: "", min: 0, max: 255, step: 1 },
  height_stale_ms: { label: "높이 만료", unit: "ms", min: 50, max: 2000, step: 50 },
  height_min: { label: "최소 높이", unit: "cm", min: 5, max: 300, step: 5 },
  height_max: { label: "최대 높이", unit: "cm", min: 10, max: 500, step: 5 },
  max_vel: { label: "속도 이상 판정", unit: "cm/s", min: 10, max: 300, step: 10 },
  flow_swap: { label: "광류 X↔Y 교환", unit: "0/1", min: 0, max: 1, step: 1 },
  flow_fwd_sign: { label: "광류 앞 부호", unit: "", min: -1, max: 1, step: 2 },
  flow_right_sign: { label: "광류 오른쪽 부호", unit: "", min: -1, max: 1, step: 2 },
  att_comp: { label: "기울기 보정", unit: "0/1", min: 0, max: 1, step: 1 },
  att_roll_sign: { label: "기울기 롤 부호", unit: "", min: -1, max: 1, step: 2 },
  att_pitch_sign: { label: "기울기 피치 부호", unit: "", min: -1, max: 1, step: 2 },
  cam_off_right: { label: "카메라→CG 오른쪽", unit: "cm", min: -50, max: 50, step: 0.5 },
  cam_off_fwd: { label: "카메라→CG 앞", unit: "cm", min: -50, max: 50, step: 0.5 },
  deadband_neutral: { label: "중앙 영역 중립", unit: "0/1", min: 0, max: 1, step: 1 },
  lost_neutral: { label: "태그 유실 중립", unit: "0/1", min: 0, max: 1, step: 1 },
  overspeed: { label: "과속 제동 기준", unit: "cm/s", min: 1, max: 12, step: 1 },
  handover_slew: { label: "수동→자동 기준점 복귀", unit: "µs/s", min: 10, max: 1000, step: 10 },
};

export type ApproachParamName = keyof typeof APPROACH_PARAMS;

/** `approach set` 한 줄. 이름은 허용목록, 값은 유한수·범위 안이어야 한다. */
export function serializeApproachSet(name: unknown, value: unknown): string {
  if (typeof name !== "string" || !Object.prototype.hasOwnProperty.call(APPROACH_PARAMS, name)) {
    throw new Error("허용되지 않은 접근 제어 파라미터입니다.");
  }
  const spec = APPROACH_PARAMS[name];
  const num = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  if (!Number.isFinite(num)) throw new Error(`${spec.label}: 숫자가 아닙니다.`);
  if (num < spec.min || num > spec.max) {
    throw new Error(`${spec.label}: ${spec.min}~${spec.max} ${spec.unit} 범위여야 합니다.`);
  }
  if (spec.unit === "0/1" && num !== 0 && num !== 1) throw new Error(`${spec.label}: 0 또는 1이어야 합니다.`);
  // 소수 3자리까지. 펌웨어는 %f 로 읽는다.
  const text = Number.isInteger(num) ? String(num) : num.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return `approach set ${name} ${text}\n`;
}

export interface ApproachView {
  mode: ApproachMode | "unknown";
  state: ApproachState | "unknown";
  fresh: boolean;
  tone: PillTone;
  label: string;
  detail: string;
  /** 최신 AUTO 보고에서 접근 출력 적용 조건이 확인됐는가. 송신 여부만으로 판정하지 않는다. */
  outputsToFc: boolean;
}

/** 접근 제어 상태 요약. 보고가 끊기면 unknown — shadow 라도 "안전"으로 표시하지 않는다. */
export function deriveApproachView(
  approach: ApproachTelemetry | undefined,
  info: InfoTelemetry | undefined,
  now = Date.now(),
  staleAfterMs = 2_000,
): ApproachView {
  const mode: ApproachMode | "unknown" = approach?.mode ?? info?.approach ?? "unknown";
  const outputMode = mode === "on" || mode === "legacy";
  if (!approach || freshness(approach.receivedAt, now, staleAfterMs) !== "fresh") {
    const haveMode = mode !== "unknown";
    return {
      mode,
      state: "unknown",
      fresh: false,
      tone: haveMode && mode === "off" ? "muted" : "amber",
      label: haveMode ? mode.toUpperCase() : "--",
      detail: mode === "off" ? "접근 제어 꺼짐 (보고 없음)" : approach ? "접근 제어 보고가 오래됐습니다." : "접근 제어 보고 없음",
      outputsToFc: false,
    };
  }
  const state = approach.state ?? "unknown";
  const fault = approach.fault ?? null;
  const outputsToFc = outputMode && approach.authority === "auto" && approach.rc_tx === true
    && approach.mask_ok === true && state !== "FAULT" && state !== "unknown";
  const dist = approach.dist_cm != null ? `${approach.dist_cm.toFixed(1)}cm` : "--";
  let tone: PillTone = "cyan";
  let detail = "";
  switch (state) {
    case "FAULT":
      tone = fault === "manual_authority" || fault === "disabled" ? "amber" : "red";
      detail = `중지 · ${fault === "mask" ? "마스크 부적합 (mask)" : fault === "mask_unknown" ? "마스크 확인 전 (mask_unknown)" : fault ?? "원인 미상"}`;
      break;
    case "HOLD":
      tone = "amber";
      detail = `태그 상실 · ${approach.neutral === true ? "중립 수렴" : "광류 제동"} (${fault ?? "tag_lost"})`;
      break;
    case "ARRIVED":
      tone = "cyan";
      detail = `도착 · 거리 ${dist}`;
      break;
    case "ARRIVING":
      tone = "cyan";
      detail = `도착 판정 중 · ${approach.arrive_held_ms ?? 0}ms 유지 · 거리 ${dist}`;
      break;
    case "APPROACH":
      tone = outputsToFc ? "cyan" : "muted";
      detail = `접근 · 거리 ${dist} · 목표 ${approach.target_speed?.toFixed(1) ?? "--"}cm/s`;
      break;
    case "IDLE":
      tone = "muted";
      detail = "대기";
      break;
    default:
      tone = "amber";
      detail = "상태 미상";
  }
  if (approach.overspeed === true) {
    detail += " · 과속 제동";
    if (tone !== "red") tone = "amber";
  }
  return {
    mode,
    state,
    fresh: true,
    tone,
    label: `${mode === "unknown" ? "--" : mode.toUpperCase()} · ${state}`,
    detail,
    outputsToFc,
  };
}

import { Activity, ArrowDownToLine, Check, ChevronDown, Crosshair, Eye, EyeOff, Focus, Gauge, Layers3, Maximize2, Pause, Play, Radio, RotateCcw, Settings2, ShieldAlert, SlidersHorizontal, Target, Timer, Wind, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { ApproachSimulation, DEMO_SCENARIOS, DemoScript, scenarioParams, summarize, type DemoScenario, type Disturbance, type SimFrame, type SimParams } from "./approachSim";
import SimulationScene from "./SimulationScene";
import "./simulator.css";

// 기존 물리 계산 20Hz 유지. 보드의 50Hz 송신과는 별개다.
const STEP_MS = 50;
const STEP_S = STEP_MS / 1000;
const COLORS = { accent: "#67e8f9", warn: "#fbbf72", danger: "#fb8d8a", muted: "#95a9bc" };
type View = "orbit" | "top" | "camera";
type Tab = "telemetry" | "environment" | "settings";
type ReplayEvent = { atMs: number; params?: Partial<SimParams>; disturbance?: Partial<Disturbance> };
type Run = { sim: ApproachSimulation; script: DemoScript; manual: boolean; nextEvent: number };

function newRun(scenario: DemoScenario, params: SimParams): Run {
  return { sim: new ApproachSimulation(scenario.startXcm, scenario.startYcm, { ...params }), script: new DemoScript(scenario), manual: false, nextEvent: 0 };
}
function applyEvents(run: Run, events: readonly ReplayEvent[]): void {
  while (run.nextEvent < events.length && events[run.nextEvent].atMs <= run.sim.tMs) {
    const event = events[run.nextEvent++];
    if (event.params) run.sim.ctrl.p = { ...run.sim.ctrl.p, ...event.params };
    if (event.disturbance) {
      run.manual = true;
      Object.assign(run.sim.disturbance, event.disturbance);
      if (event.disturbance.pilotRpy) run.sim.disturbance.pilotRpy = [...event.disturbance.pilotRpy];
    }
  }
}
function advanceRun(run: Run, events: readonly ReplayEvent[], targetMs: number): void {
  while (run.sim.tMs < targetMs) {
    applyEvents(run, events);
    if (!run.manual) run.script.apply(run.sim);
    run.sim.step(STEP_S);
  }
  // 일시정지 중 입력은 즉시 표시하고, 제어기 출력은 다음 물리 스텝에서 갱신한다.
  applyEvents(run, events);
  if (!run.manual) run.script.apply(run.sim);
}
/** 시드·각본·변경 기록으로 동일 시점의 물리를 재계산한다. 기록을 보간하지 않는다. */
export function replaySimulation(scenario: DemoScenario, params: SimParams, events: readonly ReplayEvent[], targetMs: number): Run {
  const run = newRun(scenario, params);
  advanceRun(run, events, Math.max(0, Math.min(scenario.durationMs, Math.floor(targetMs / STEP_MS) * STEP_MS)));
  return run;
}
const fmt = (value: number | undefined, digits = 1) => value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
const signed = (value: number | undefined) => value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
const timeLabel = (ms: number) => `${(ms / 1000).toFixed(1)}초`;
const rpy = (value: readonly number[] | undefined) => value?.map((v) => fmt(v, 0)).join(" / ") ?? "—";

function stateLabel(frame: SimFrame | null): { label: string; tone: string } {
  if (!frame) return { label: "시험 준비", tone: "ready" };
  const out = frame.out;
  switch (out.state) {
    case "ARRIVED": return { label: "도착 확인", tone: "good" };
    case "ARRIVING": return { label: out.neutral ? "중앙 영역 · 중립 수렴" : "감속 접근", tone: "ready" };
    case "APPROACH": return { label: out.overspeed ? "과속 감지 · 제동" : "태그 접근 중", tone: out.overspeed ? "warning" : "ready" };
    case "HOLD": return { label: out.neutral ? "태그 상실 · 중립 수렴" : "태그 상실 · 광류 제동", tone: "warning" };
    case "FAULT": return { label: ({ manual_authority: "수동 제어", kill: "킬 · 접근 중지", flow: "광류 저하 · 접근 중지", height: "높이 범위 이탈", mask: "마스크 오류 · 접근 중지", mask_unknown: "마스크 미확인", velocity: "속도 이상 · 접근 중지" } as Record<string, string>)[out.fault ?? ""] ?? "제어기 중지", tone: out.fault === "manual_authority" ? "warning" : "danger" };
    default: return { label: "제어기 대기", tone: "ready" };
  }
}
function prepareCanvas(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d"); if (!ctx) return null;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 300; const height = canvas.clientHeight || 140;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) { canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}
function drawChart(canvas: HTMLCanvasElement, frames: readonly SimFrame[], durationMs: number) {
  const plot = prepareCanvas(canvas); if (!plot) return;
  const { ctx, width: W, height: H } = plot;
  const left = 30; const right = W - 8; const top = 12; const bottom = H - 24;
  const observedMax = frames.reduce((max, f) => Math.max(max, Math.hypot(f.vx, f.vy), f.out.targetSpeedCmS), 10);
  const tickStep = Math.max(5, Math.ceil(observedMax / 25) * 5);
  const vMax = Math.max(15, Math.ceil(observedMax / tickStep) * tickStep);
  const X = (ms: number) => left + ms / durationMs * (right - left);
  const Y = (v: number) => bottom - v / vMax * (bottom - top);
  ctx.font = "10px system-ui, sans-serif"; ctx.fillStyle = COLORS.muted;
  for (let v = 0; v <= vMax; v += tickStep) { ctx.strokeStyle = "rgba(173,199,219,.12)"; ctx.beginPath(); ctx.moveTo(left, Y(v)); ctx.lineTo(right, Y(v)); ctx.stroke(); ctx.fillText(String(v), 3, Y(v) + 4); }
  ctx.fillText("cm/s", 1, 9); ctx.fillText("0", left, H - 5); ctx.fillText(`${durationMs / 1000}s`, right - 22, H - 5);
  for (let i = 1; i < frames.length; i++) { const f = frames[i]; if (f.out.state !== "HOLD" && f.out.state !== "FAULT") continue; ctx.fillStyle = f.out.state === "FAULT" ? "rgba(251,141,138,.10)" : "rgba(251,191,114,.10)"; ctx.fillRect(X(frames[i - 1].tMs), top, Math.max(1, X(f.tMs) - X(frames[i - 1].tMs)), bottom - top); }
  const line = (pick: (f: SimFrame) => number, color: string, dash: number[], scale = vMax) => { ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = 1.8; ctx.beginPath(); frames.forEach((f, index) => { const x = X(f.tMs); const y = bottom - Math.min(pick(f), scale) / scale * (bottom - top); if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y); }); ctx.stroke(); ctx.setLineDash([]); };
  line((f) => Math.hypot(f.x, f.y), "#7d94aa", [], 80);
  line((f) => f.out.targetSpeedCmS, COLORS.warn, [4, 4]);
  line((f) => Math.hypot(f.vx, f.vy), COLORS.accent, []);
  ctx.setLineDash([2, 4]); ctx.strokeStyle = COLORS.danger; ctx.beginPath(); ctx.moveTo(left, Y(10)); ctx.lineTo(right, Y(10)); ctx.stroke(); ctx.setLineDash([]);
}
function drawHandoverChart(canvas: HTMLCanvasElement, frames: readonly SimFrame[]) {
  const plot = prepareCanvas(canvas); if (!plot) return;
  const { ctx, width: W, height: H } = plot;
  const X = (ms: number) => 38 + Math.min(ms, 5000) / 5000 * (W - 48);
  const visible = frames.filter((f) => f.tMs <= 5000);
  const spread = Math.max(30, ...visible.flatMap((f) => f.baseRpy.map((v) => Math.abs(v - 1500))));
  const Y = (us: number) => 12 + (1500 + spread - us) / (spread * 2) * (H - 34);
  ctx.font = "10px system-ui, sans-serif"; ctx.fillStyle = COLORS.muted; ctx.fillText("1500", 2, Y(1500) + 3); ctx.fillText("µs", 3, 10); ctx.fillText("5s", W - 20, H - 3);
  ctx.setLineDash([3, 4]); ctx.strokeStyle = "#60788d"; ctx.beginPath(); ctx.moveTo(X(0), Y(1500)); ctx.lineTo(X(5000), Y(1500)); ctx.stroke();
  [COLORS.accent, COLORS.warn, "#e2eaf0"].forEach((color, axis) => { ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(axis === 0 ? [] : axis === 1 ? [5, 3] : [1, 3]); ctx.beginPath(); visible.forEach((f, index) => { if (index) ctx.lineTo(X(f.tMs), Y(f.baseRpy[axis])); else ctx.moveTo(X(f.tMs), Y(f.baseRpy[axis])); }); ctx.stroke(); }); ctx.setLineDash([]);
}

interface SliderSpec { key: keyof SimParams; label: string; min: number; max: number; step: number; unit: string }
const PRIMARY_SLIDERS: SliderSpec[] = [
  { key: "maxSpeedCmS", label: "목표 속도 상한", min: 2, max: 10, step: 1, unit: "cm/s" },
  { key: "slowRadiusCm", label: "감속 시작 거리", min: 10, max: 60, step: 5, unit: "cm" },
  { key: "maxAccelCmS2", label: "목표 가속도 제한", min: 1, max: 100, step: 1, unit: "cm/s²" },
  { key: "velKpUsPerCmS", label: "속도 P 게인", min: 0, max: 20, step: 0.1, unit: "µs/(cm/s)" },
  { key: "velKiUsPerCm", label: "속도 I 게인", min: 0, max: 10, step: 0.1, unit: "µs/cm" },
  { key: "overspeedCmS", label: "실측 과속 제동 기준", min: 1, max: 20, step: 1, unit: "cm/s" },
];
const ADVANCED: { title: string; sliders: SliderSpec[] }[] = [
  { title: "도착 판정", sliders: [
    { key: "stopRadiusCm", label: "중립 영역 반경", min: 0, max: 50, step: 1, unit: "cm" },
    { key: "arriveTolCm", label: "도착 거리 허용", min: 1, max: 50, step: 1, unit: "cm" },
    { key: "arriveVelCmS", label: "도착 속도 허용", min: 0.5, max: 20, step: 0.5, unit: "cm/s" },
    { key: "arriveHoldMs", label: "도착 유지 시간", min: 100, max: 10000, step: 100, unit: "ms" },
  ] },
  { title: "출력·핸드오버", sliders: [
    { key: "velIMaxUs", label: "적분 상한", min: 0, max: 80, step: 1, unit: "µs" },
    { key: "cmdMaxUs", label: "롤·피치 오프셋 상한", min: 10, max: 100, step: 5, unit: "µs" },
    { key: "cmdSlewUsPerS", label: "명령 변화율", min: 10, max: 1000, step: 10, unit: "µs/s" },
    { key: "handoverSlewUsPerS", label: "기준점 중립 복귀율", min: 10, max: 1000, step: 10, unit: "µs/s" },
  ] },
  { title: "센서·고장 기준", sliders: [
    { key: "tagStaleMs", label: "태그 만료 시간", min: 100, max: 5000, step: 100, unit: "ms" },
    { key: "flowStaleMs", label: "광류 만료 시간", min: 50, max: 2000, step: 50, unit: "ms" },
    { key: "flowMinQuality", label: "광류 최소 품질", min: 0, max: 255, step: 1, unit: "q" },
    { key: "heightStaleMs", label: "높이 만료 시간", min: 50, max: 2000, step: 50, unit: "ms" },
    { key: "heightMinCm", label: "최소 높이", min: 5, max: 300, step: 5, unit: "cm" },
    { key: "heightMaxCm", label: "최대 높이", min: 10, max: 500, step: 5, unit: "cm" },
    { key: "maxVelCmS", label: "속도 이상 중지 기준", min: 10, max: 300, step: 10, unit: "cm/s" },
  ] },
];
function Readout({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return <div className={`simlab-readout${warning ? " is-warning" : ""}`}><dt>{label}</dt><dd>{value}</dd></div>;
}

export default function ApproachSimulator({ onClose }: { onClose: () => void }) {
  const initialScenario = DEMO_SCENARIOS[0];
  const runRef = useRef<Run | null>(null);
  if (!runRef.current) runRef.current = newRun(initialScenario, scenarioParams(initialScenario));
  const initialParamsRef = useRef(scenarioParams(initialScenario));
  const eventsRef = useRef<ReplayEvent[]>([]);
  const [revision, setRevision] = useState(0);
  const [running, setRunning] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [speed, setSpeed] = useState(1);
  const [view, setView] = useState<View>("orbit");
  const [showGuides, setShowGuides] = useState(true);
  const [resetKey, setResetKey] = useState(0);
  const [tab, setTab] = useState<Tab>("telemetry");
  const [notice, setNotice] = useState("");
  const [sceneReady, setSceneReady] = useState<boolean | null>(null);
  const reducedMotionRef = useRef(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const chartRef = useRef<HTMLCanvasElement>(null);
  const handoverChartRef = useRef<HTMLCanvasElement>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const onCloseRef = useRef(onClose); onCloseRef.current = onClose;
  const publish = useCallback(() => setRevision((value) => value + 1), []);
  const onSceneReady = useCallback((ready: boolean) => setSceneReady(ready), []);

  const resetRun = (scenario = runRef.current!.script.scenario, params = runRef.current!.sim.ctrl.p, autoplay = true) => {
    initialParamsRef.current = { ...params }; eventsRef.current = [];
    runRef.current = newRun(scenario, params); advanceRun(runRef.current, [], 0);
    setNotice(""); setResetKey((value) => value + 1); setRunning(autoplay && !reducedMotionRef.current); publish();
  };
  const selectScenario = (id: string) => {
    const scenario = DEMO_SCENARIOS.find((item) => item.id === id) ?? initialScenario;
    resetRun(scenario, scenarioParams(scenario));
  };
  const seek = (ms: number) => {
    const current = runRef.current!;
    runRef.current = replaySimulation(current.script.scenario, initialParamsRef.current, eventsRef.current, ms);
    setRunning(false); setNotice(""); publish();
  };
  const recordChange = (change: Omit<ReplayEvent, "atMs">) => {
    let current = runRef.current!;
    if (current.script.finished(current.sim)) {
      initialParamsRef.current = { ...current.sim.ctrl.p }; eventsRef.current = [];
      current = newRun(current.script.scenario, initialParamsRef.current); runRef.current = current;
      setNotice("완료된 시험을 처음부터 다시 시작해 변경을 적용했습니다."); setRunning(!reducedMotionRef.current);
    } else setNotice(running ? "변경을 적용했습니다. 현재 시점부터 기록합니다." : "입력 변경 완료 · 재생하면 다음 제어 스텝에 반영됩니다.");
    const atMs = current.sim.tMs;
    // 과거 시점에서 편집하면 이후 입력만 교체하며 이전 입력은 유지한다.
    eventsRef.current = [...eventsRef.current.filter((event) => event.atMs <= atMs), { atMs, ...change }];
    runRef.current = replaySimulation(current.script.scenario, initialParamsRef.current, eventsRef.current, atMs); publish();
  };
  const setParam = (key: keyof SimParams, value: number) => recordChange({ params: { [key]: key === "maxSpeedCmS" ? Math.min(10, value) : value } });
  const togglePlayback = () => {
    const current = runRef.current!;
    if (current.script.finished(current.sim)) { runRef.current = replaySimulation(current.script.scenario, initialParamsRef.current, eventsRef.current, 0); publish(); setRunning(true); setNotice("같은 입력 기록으로 시험을 다시 재생합니다."); }
    else setRunning((value) => !value);
  };
  const togglePlaybackRef = useRef(togglePlayback); togglePlaybackRef.current = togglePlayback;

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; closeButtonRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onCloseRef.current(); return; }
      const target = event.target as HTMLElement | null;
      if (event.key === " " && !target?.closest("input, select, textarea, button, a, summary, [contenteditable=true]")) { event.preventDefault(); togglePlaybackRef.current(); }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex='0']") ?? []).filter((element) => element.offsetParent !== null);
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKey, true);
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onPreference = () => { reducedMotionRef.current = preference.matches; if (preference.matches) setRunning(false); };
    preference.addEventListener("change", onPreference);
    return () => { window.removeEventListener("keydown", onKey, true); preference.removeEventListener("change", onPreference); document.body.style.overflow = previousOverflow; if (previousFocus?.isConnected) previousFocus.focus(); };
  }, []);
  useEffect(() => {
    if (!running) return;
    let raf = 0; let previous: number | null = null; let accumulator = 0;
    const animate = (now: number) => {
      const current = runRef.current!;
      if (previous != null) accumulator += Math.min(0.25, (now - previous) / 1000) * speed;
      previous = now; let changed = false;
      while (accumulator >= STEP_S && !current.script.finished(current.sim)) { accumulator -= STEP_S; advanceRun(current, eventsRef.current, current.sim.tMs + STEP_MS); changed = true; }
      if (changed) publish();
      if (current.script.finished(current.sim)) { setRunning(false); return; }
      raf = requestAnimationFrame(animate);
    };
    raf = requestAnimationFrame(animate); return () => cancelAnimationFrame(raf);
  }, [running, speed, publish]);
  const drawPlots = useCallback(() => {
    const current = runRef.current!;
    if (chartRef.current) drawChart(chartRef.current, current.sim.frames, current.script.scenario.durationMs);
    if (handoverChartRef.current) drawHandoverChart(handoverChartRef.current, current.sim.frames);
  }, []);
  useEffect(drawPlots, [revision, tab, drawPlots]);
  useEffect(() => {
    const observer = new ResizeObserver(drawPlots);
    if (chartRef.current) observer.observe(chartRef.current);
    if (handoverChartRef.current) observer.observe(handoverChartRef.current);
    return () => observer.disconnect();
  }, [tab, resetKey, drawPlots]);

  const run = runRef.current!; const sim = run.sim; const scenario = run.script.scenario;
  const params = sim.ctrl.p; const d = sim.disturbance;
  const frame = sim.frames[sim.frames.length - 1] ?? null;
  const sceneFrame = frame ? { ...frame, disturbance: { ...d, pilotRpy: [...d.pilotRpy] as [number, number, number] } } : null;
  const out = frame?.out; const state = stateLabel(frame);
  const completed = run.script.finished(sim); const summary = completed ? summarize(sim.frames) : null;
  const actualSpeed = frame ? Math.hypot(frame.vx, frame.vy) : 0;
  const distance = frame ? Math.hypot(frame.x, frame.y) : Math.hypot(scenario.startXcm, scenario.startYcm);
  const caption = run.manual ? "직접 시험 중 · 외란과 제어 설정을 기록하며 재생합니다." : run.script.caption || scenario.summary;
  const tabs: { id: Tab; label: string; icon: typeof Activity }[] = [{ id: "telemetry", label: "실시간 계측", icon: Activity }, { id: "environment", label: "환경·고장", icon: Wind }, { id: "settings", label: "제어 설정", icon: Settings2 }];
  const renderSlider = (spec: SliderSpec) => <label className="simlab-slider" key={spec.key}><span>{spec.label}<output>{fmt(params[spec.key], spec.step < 1 ? 1 : 0)} <small>{spec.unit}</small></output></span><input aria-label={spec.label} type="range" min={spec.min} max={spec.max} step={spec.step} value={params[spec.key]} onChange={(event) => setParam(spec.key, Number(event.target.value))} /></label>;

  return <div className="simlab" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="simlab-title">
    <header className="simlab-header">
      <div className="simlab-brand"><span className="simlab-brand-icon"><Crosshair size={24} /></span><div><span className="simlab-eyebrow">가상 비행 시험실</span><h1 id="simlab-title">접근 시뮬레이터</h1></div></div>
      <label className="simlab-scenario-picker"><span>시험 각본 <small>{String(DEMO_SCENARIOS.findIndex((item) => item.id === scenario.id) + 1).padStart(2, "0")} / {String(DEMO_SCENARIOS.length).padStart(2, "0")}</small></span><select aria-label="시연 각본" value={scenario.id} onChange={(event) => selectScenario(event.target.value)}>{DEMO_SCENARIOS.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><ChevronDown size={16} aria-hidden="true" /></label>
      <div className="simlab-header-actions"><span className="simlab-safety"><i />SIMULATION · 실기기 아님</span><button ref={closeButtonRef} type="button" className="simlab-icon-button" onClick={onClose} aria-label="시뮬레이터 닫기" title="닫기 (Esc)"><X size={22} /></button></div>
    </header>
    <div className="simlab-layout">
      <main className="simlab-main">
        <div className="simlab-brief"><div><span className="simlab-section-label">시험 목적</span><p>{scenario.summary}</p></div><span className={`simlab-mode-tag ${run.manual ? "is-manual" : ""}`}>{run.manual ? "직접 시험" : "각본 재생"}</span></div>
        <div className="simlab-metrics" aria-label="주요 계측값">
          <div className={actualSpeed > 10 ? "is-warning" : ""}><span><Gauge size={15} />모델 속도</span><strong>{fmt(actualSpeed)}<small>cm/s</small></strong><em>{actualSpeed > 10 ? "목표 상한 10 초과" : "목표 상한 10 cm/s"}</em></div>
          <div><span><Target size={15} />태그까지 거리</span><strong>{fmt(distance)}<small>cm</small></strong><em>도착 허용 {params.arriveTolCm} cm</em></div>
          <div><span><ArrowDownToLine size={15} />모델 고도</span><strong>{fmt(sim.plant.heightCm, 0)}<small>cm</small></strong><em>고정 높이 · 수평 시험</em></div>
          <div><span><Timer size={15} />경과 시간</span><strong>{fmt(sim.tMs / 1000)}<small>s</small></strong><em>총 {scenario.durationMs / 1000}초 · {speed}× 재생</em></div>
        </div>
        <section className="simlab-stage" aria-label="3차원 가상 시험 장면">
          <div className="simlab-stage-toolbar"><div className="simlab-view-switch" role="group" aria-label="카메라 시점">{([{ id: "orbit", label: "입체", icon: Layers3 }, { id: "top", label: "상공", icon: Maximize2 }, { id: "camera", label: "하향 카메라", icon: Eye }] as const).map(({ id, label, icon: Icon }) => <button key={id} type="button" className={view === id ? "is-active" : ""} aria-pressed={view === id} onClick={() => setView(id)}><Icon size={15} />{label}</button>)}</div><div className="simlab-stage-actions"><button type="button" className={showGuides ? "is-active" : ""} aria-pressed={showGuides} onClick={() => setShowGuides((value) => !value)} title="시야·거리 가이드 표시"><Crosshair size={15} /><span>가이드</span></button><button type="button" onClick={() => setResetKey((value) => value + 1)} title="카메라 시점 초기화" aria-label="카메라 시점 초기화"><Focus size={17} /></button></div></div>
          <div className="simlab-viewport">
            <SimulationScene frame={sceneFrame} frames={sim.frames} heightCm={sim.plant.heightCm} startXcm={scenario.startXcm} startYcm={scenario.startYcm} view={view} showGuides={showGuides} running={running} resetKey={resetKey} onReady={onSceneReady} />
            {sceneReady === false && <div className="simlab-render-error"><ShieldAlert size={18} /><span>3D 가속을 사용할 수 없어 평면 장면으로 표시합니다.</span></div>}
            <div className="simlab-scene-label"><i className={running ? "is-running" : ""} />{running ? "모델 실행 중" : completed ? "시험 완료" : "일시정지"}<span>가상 시험장</span></div>
            <div className="simlab-scene-caption"><span className={`simlab-status ${state.tone}`}>{state.label}</span><p>{caption}</p></div>
          </div>
          <div className="simlab-stage-foot"><span>{view === "orbit" ? "드래그로 회전 · 휠로 확대" : view === "top" ? "태그를 원점으로 본 상공 시점" : "기체 아래를 보는 가상 카메라"}</span><span>자세·카메라는 시각적 근사</span></div>
        </section>
        <section className="simlab-replay" aria-label="시험 재생 조작">
          <div className="simlab-timeline-head"><span>{run.manual ? "입력 기록 타임라인" : "각본 타임라인"}</span><output>{timeLabel(sim.tMs)}<span> / {timeLabel(scenario.durationMs)}</span></output></div>
          <div className="simlab-timeline"><input type="range" min={0} max={scenario.durationMs} step={STEP_MS} value={sim.tMs} aria-label="시험 재생 시점" aria-valuetext={timeLabel(sim.tMs)} onChange={(event) => seek(Number(event.target.value))} style={{ "--progress": `${sim.tMs / scenario.durationMs * 100}%` } as CSSProperties} /><div className="simlab-cue-track">{scenario.cues.map((cue, index) => { const text = cue.actions.find((action) => action.kind === "caption"); return <button key={`${cue.atMs}-${index}`} type="button" className={cue.atMs <= sim.tMs ? "is-past" : ""} style={{ left: `${cue.atMs / scenario.durationMs * 100}%` }} onClick={() => seek(cue.atMs)} title={`${timeLabel(cue.atMs)} · ${text?.kind === "caption" ? text.text : "각본 이벤트"}`} aria-label={`${timeLabel(cue.atMs)} 이벤트로 이동`}><i /><span>{cue.atMs / 1000}s</span></button>; })}</div></div>
          <div className="simlab-playback"><div className="simlab-playback-left"><button className="simlab-play" type="button" onClick={togglePlayback}>{running ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}{running ? "일시정지" : completed ? "다시 재생" : sim.tMs === 0 ? "시험 시작" : "계속 재생"}</button><button type="button" className="simlab-reset" onClick={() => resetRun()} title="현재 설정으로 처음부터 실행"><RotateCcw size={16} /><span>초기화</span></button></div><div className="simlab-speed" role="group" aria-label="재생 배속">{[1, 2, 4].map((value) => <button key={value} type="button" aria-pressed={speed === value} className={speed === value ? "is-active" : ""} onClick={() => setSpeed(value)}>{value}×</button>)}</div><span className="simlab-key-hint"><kbd>Space</kbd> 재생 <kbd>Esc</kbd> 닫기</span></div>
          {notice && <p className="simlab-notice" role="status">{notice}</p>}
        </section>
      </main>
      <aside className="simlab-sidebar" aria-label="시험 계측과 설정">
        <div className="simlab-tabs" role="tablist" aria-label="시험 도구">{tabs.map(({ id, label, icon: Icon }, index) => <button key={id} id={`simlab-tab-${id}`} type="button" role="tab" ref={(element) => { tabRefs.current[index] = element; }} aria-controls={`simlab-panel-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={(event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length; setTab(tabs[next].id); tabRefs.current[next]?.focus(); }}><Icon size={16} /><span>{label}</span></button>)}</div>
        <div className="simlab-panel" role="tabpanel" id={`simlab-panel-${tab}`} aria-labelledby={`simlab-tab-${tab}`} tabIndex={0}>
          {tab === "telemetry" && <>
            <section className="simlab-card"><div className="simlab-card-head"><h2>제어 상태</h2><span className={`simlab-status ${state.tone}`}>{out?.state ?? "READY"}</span></div><p className="simlab-state-description">{state.label}</p><dl className="simlab-readouts"><Readout label="CH6 제어권" value={d.authorityAuto ? "AUTO · 보드 수평 제어" : "MANUAL · 조종기 제어"} /><Readout label="태그 인식" value={frame ? frame.tagVisible ? "태그 추적 중" : d.tagOccluded ? "가림 · 재포착 대기" : "시야 밖" : "관측 대기"} warning={Boolean(frame && !frame.tagVisible)} /><Readout label="광류 품질" value={`q ${d.flowQuality} / 기준 ${params.flowMinQuality}`} warning={d.flowQuality < params.flowMinQuality} /><Readout label="도착 유지" value={`${out?.arriveHeldMs ?? 0} / ${params.arriveHoldMs} ms`} /></dl></section>
            <section className="simlab-card simlab-chart-card"><div className="simlab-card-head"><h2>속도·거리 기록</h2><span className="simlab-unit">모델 계산값</span></div><canvas ref={chartRef} aria-label="실제 모델 속도, 목표 프로파일과 거리 시계열" /><div className="simlab-legend"><span><i className="actual" />모델 속도</span><span><i className="target" />프로파일 목표</span><span><i className="distance" />거리 0~80cm</span></div><p className="simlab-help">빨간 점선은 목표 상한 10cm/s입니다. 관성·외란으로 모델 속도는 이를 넘을 수 있습니다.</p></section>
            <section className="simlab-card"><div className="simlab-card-head"><h2>제어기 출력</h2><span className="simlab-unit">펌웨어 −25 기준</span></div><dl className="simlab-readouts"><Readout label="목표 속도 · 오른쪽 / 앞" value={`${signed(out?.vTargetRightCmS)} / ${signed(out?.vTargetFwdCmS)} cm/s`} /><Readout label="광류 속도 · 오른쪽 / 앞" value={`${signed(frame?.measVelRight)} / ${signed(frame?.measVelFwd)} cm/s`} /><Readout label="롤 / 피치 오프셋" value={`${signed(out?.rollUs)} / ${signed(out?.pitchUs)} µs`} /><Readout label="중립 / 과속 제동" value={out ? `${out.neutral ? "중립 수렴" : "중립 조건 아님"} / ${out.overspeed ? "제동 중" : "제동 아님"}` : "계산 대기"} warning={out?.overspeed} /></dl></section>
            {scenario.id === "handover" && <section className="simlab-card"><div className="simlab-card-head"><h2>핸드오버 기준점</h2><span className="simlab-unit">R / P / Y</span></div><dl className="simlab-readouts"><Readout label="조종기 입력" value={`${rpy(d.pilotRpy)} µs`} /><Readout label="기준점" value={`${rpy(frame?.baseRpy)} µs`} /><Readout label="합산 출력" value={`${rpy(frame?.outRpy)} µs`} /></dl><canvas ref={handoverChartRef} aria-label="첫 5초 롤·피치·요 기준점의 1500 마이크로초 수렴" /><p className="simlab-help">롤 실선 · 피치 파선 · 요 점선. 문서 기반 시각 모델이며 실제 FC 지연 검증은 포함하지 않습니다.</p></section>}
          </>}
          {tab === "environment" && <>
            <div className="simlab-panel-heading"><span className="simlab-section-label">조건을 바꿔 관찰하세요</span><h2>환경·고장 주입</h2><p>첫 외란을 넣으면 이후 각본 이벤트는 멈추고 직접 시험으로 전환됩니다. 일시정지 중에도 입력을 바꿀 수 있습니다.</p></div>
            <div className="simlab-disturbances">
              <button type="button" aria-pressed={d.tagOccluded} className={d.tagOccluded ? "is-active" : ""} onClick={() => recordChange({ disturbance: { tagOccluded: !d.tagOccluded } })}>{d.tagOccluded ? <EyeOff /> : <Eye />}<strong>{d.tagOccluded ? "태그 다시 보이기" : "태그 가리기"}</strong><small>{d.tagOccluded ? "현재 가림" : "시야 상실 시험"}</small></button>
              <button type="button" aria-pressed={!d.authorityAuto} className={!d.authorityAuto ? "is-active" : ""} onClick={() => recordChange({ disturbance: { authorityAuto: !d.authorityAuto } })}><Radio /><strong>{d.authorityAuto ? "수동으로 전환" : "자동으로 전환"}</strong><small>CH6 {d.authorityAuto ? "AUTO" : "MANUAL"}</small></button>
              <button type="button" aria-pressed={d.flowQuality < params.flowMinQuality} className={d.flowQuality < params.flowMinQuality ? "is-active" : ""} onClick={() => recordChange({ disturbance: { flowQuality: d.flowQuality < params.flowMinQuality ? 58 : 22 } })}><Activity /><strong>{d.flowQuality < params.flowMinQuality ? "광류 품질 복구" : "광류 품질 저하"}</strong><small>현재 q {d.flowQuality}</small></button>
              <button type="button" aria-pressed={d.windRightCmS2 !== 0} className={d.windRightCmS2 !== 0 ? "is-active" : ""} onClick={() => recordChange({ disturbance: { windRightCmS2: d.windRightCmS2 ? 0 : 5, windFwdCmS2: 0 } })}><Wind /><strong>{d.windRightCmS2 ? "옆바람 끄기" : "옆바람 넣기"}</strong><small>{d.windRightCmS2 ? `${d.windRightCmS2} cm/s²` : "오른쪽 5 cm/s²"}</small></button>
              <button type="button" aria-pressed={!d.maskOk} className={!d.maskOk ? "is-active" : ""} onClick={() => recordChange({ disturbance: { maskOk: !d.maskOk } })}><SlidersHorizontal /><strong>{d.maskOk ? "마스크 오류" : "마스크 복구"}</strong><small>{d.maskOk ? "정상 보고" : "mask 고장 주입 중"}</small></button>
              <button type="button" aria-pressed={d.kill} className={`simlab-kill ${d.kill ? "is-active" : ""}`} onClick={() => recordChange({ disturbance: { kill: !d.kill } })}><ShieldAlert /><strong>{d.kill ? "가상 킬 해제" : "가상 킬"}</strong><small>시뮬레이터만 중지</small></button>
            </div>
            <div className="simlab-information"><ShieldAlert size={18} /><p>보드·FC에 명령을 전송하지 않습니다. 고도·자세·지면 영상은 모델 가정이며 실기기 비행 성능을 보증하지 않습니다.</p></div>
          </>}
          {tab === "settings" && <>
            <div className="simlab-panel-heading"><span className="simlab-section-label">모델 파라미터</span><h2>제어 반응 조정</h2><p>실행 중 변경을 즉시 기록합니다. 최대 목표 속도는 10cm/s까지이며 실제 속도는 별도로 관찰합니다.</p></div>
            <section className="simlab-card simlab-sliders">{PRIMARY_SLIDERS.map(renderSlider)}</section>
            <section className="simlab-card simlab-switches"><label><span><strong>중앙 영역에서 중립 복귀</strong><small>끄면 속도 0을 유지하도록 제동</small></span><input type="checkbox" checked={params.deadbandNeutral !== 0} onChange={(event) => setParam("deadbandNeutral", event.target.checked ? 1 : 0)} /></label><label><span><strong>태그 상실 시 중립 복귀</strong><small>끄면 광류 속도로 제동</small></span><input type="checkbox" checked={params.lostNeutral !== 0} onChange={(event) => setParam("lostNeutral", event.target.checked ? 1 : 0)} /></label></section>
            {ADVANCED.map((group) => <details className="simlab-advanced" key={group.title}><summary>{group.title}<ChevronDown size={16} /></summary><div className="simlab-sliders">{group.sliders.map(renderSlider)}</div></details>)}
            <details className="simlab-advanced"><summary>기체 축·부호 시험<ChevronDown size={16} /></summary><div className="simlab-switches"><label><span><strong>롤 부호 반전</strong><small>부호가 틀릴 때 접근 방향 관찰</small></span><input type="checkbox" checked={params.rollSign < 0} onChange={(event) => setParam("rollSign", event.target.checked ? -1 : 1)} /></label><label><span><strong>피치 부호 반전</strong><small>앞뒤 방향의 제어 반응 관찰</small></span><input type="checkbox" checked={params.pitchSign < 0} onChange={(event) => setParam("pitchSign", event.target.checked ? -1 : 1)} /></label></div></details>
            <button type="button" className="simlab-defaults" onClick={() => { recordChange({ params: scenarioParams(scenario) }); setNotice("각본 기본 파라미터로 되돌렸습니다. 기존 외란 조건은 유지합니다."); }}><RotateCcw size={15} />각본 기본값으로 복원</button><p className="simlab-help">스틱 1µs = 1cm/s², 자세 지연 0.3초는 실측 전 후보값입니다. 3D 자세와 카메라 영상은 이 물리 모델을 설명하는 시각적 근사입니다.</p>
          </>}
        </div>
        {summary && <section className={`simlab-result ${summary.arrived ? "is-arrived" : ""}`} aria-label="시험 결과"><div><span className="simlab-result-icon">{summary.arrived ? <Check size={21} /> : <Target size={21} />}</span><div><span className="simlab-section-label">시험 결과</span><h2>{summary.arrived ? "도착 확인" : "종료 시 미도착"}</h2></div><button type="button" onClick={togglePlayback} aria-label="같은 시험 다시 재생"><RotateCcw size={17} /></button></div><dl><div><dt>최종 거리</dt><dd>{fmt(summary.finalDistCm)}<small>cm</small></dd></div><div className={summary.maxSpeedCmS > 10 ? "is-warning" : ""}><dt>최대 모델 속도</dt><dd>{fmt(summary.maxSpeedCmS)}<small>cm/s</small></dd></div><div><dt>첫 도착</dt><dd>{summary.arrivedAtMs == null ? "—" : fmt(summary.arrivedAtMs / 1000)}<small>s</small></dd></div></dl>{summary.maxSpeedCmS > 10 && <p>목표 상한 10cm/s를 넘은 구간이 있습니다.</p>}<span className="simlab-result-detail">최대 명령 {fmt(summary.maxCmdUs, 0)}µs · 고장 중지 {summary.faultCount}회 · 태그 상실 {summary.holdCount}회</span></section>}
        <footer className="simlab-sidebar-foot"><ShieldAlert size={14} /><span>시뮬레이션 전용 · 보드/FC 전송 없음</span></footer>
      </aside>
    </div>
  </div>;
}

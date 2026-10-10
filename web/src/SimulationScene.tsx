import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import type { SimFrame } from "./approachSim";
import "./simulationScene.css";

export interface SimulationSceneProps {
  frame: SimFrame | null;
  frames: readonly SimFrame[];
  heightCm: number;
  startXcm: number;
  startYcm: number;
  view: "orbit" | "top" | "camera";
  showGuides: boolean;
  running: boolean;
  resetKey: number;
  onReady?: (ok: boolean) => void;
}

// 좌표 계약: 시뮬레이션 오른쪽 x → Three x, 앞 y → Three -z, 높이 → Three y. 100cm = 1m.
const positionOf = (p: SimulationSceneProps) => new THREE.Vector3((p.frame?.x ?? p.startXcm) / 100, p.heightCm / 100, -(p.frame?.y ?? p.startYcm) / 100);
const clamp = THREE.MathUtils.clamp;

function canvasTexture(size: number, paint: (ctx: CanvasRenderingContext2D, size: number) => void): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D를 사용할 수 없습니다.");
  paint(ctx, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

function concreteTexture(): THREE.CanvasTexture {
  const texture = canvasTexture(512, (ctx, n) => {
    const data = ctx.createImageData(n, n);
    let seed = 6813;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < data.data.length; i += 4) {
      const x = (i / 4) % n; const y = Math.floor(i / (4 * n));
      const grain = (random() - 0.5) * 21 + 5 * Math.sin(x * 0.043 + y * 0.014) + 4 * Math.sin(y * 0.059);
      data.data[i] = 160 + grain; data.data[i + 1] = 162 + grain; data.data[i + 2] = 157 + grain; data.data[i + 3] = 255;
    }
    ctx.putImageData(data, 0, 0);
    for (let i = 0; i < 550; i++) {
      ctx.fillStyle = `rgba(42,49,50,${0.025 + random() * 0.05})`;
      ctx.beginPath(); ctx.ellipse(random() * n, random() * n, random() * 5 + 0.3, random() * 2 + 0.3, random() * Math.PI, 0, Math.PI * 2); ctx.fill();
    }
    for (let i = 0; i < 24; i++) {
      const x = random() * n; const y = random() * n; const radius = 20 + random() * 90;
      const wash = ctx.createRadialGradient(x, y, 0, x, y, radius);
      wash.addColorStop(0, i % 2 ? "rgba(231,229,213,.075)" : "rgba(53,65,61,.055)"); wash.addColorStop(1, "rgba(128,128,128,0)");
      ctx.fillStyle = wash; ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    }
    ctx.strokeStyle = "rgba(51,60,57,.22)"; ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, n - 2, n - 2);
  });
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(8, 8);
  return texture;
}

function carbonTexture(): THREE.CanvasTexture {
  const texture = canvasTexture(128, (ctx, n) => {
    ctx.fillStyle = "#20272d"; ctx.fillRect(0, 0, n, n);
    for (let x = 0; x < n; x += 8) for (let y = 0; y < n; y += 8) {
      const horizontal = ((x + y) / 8) % 2 === 0;
      ctx.fillStyle = horizontal ? "#343b40" : "#1b2227"; ctx.fillRect(x, y, 8, 8);
      ctx.strokeStyle = "rgba(153,168,174,.12)"; ctx.lineWidth = 0.6;
      for (let k = 1; k < 8; k += 2) { ctx.beginPath(); ctx.moveTo(x + (horizontal ? 0 : k), y + (horizontal ? k : 0)); ctx.lineTo(x + (horizontal ? 8 : k), y + (horizontal ? k : 8)); ctx.stroke(); }
    }
  });
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(3, 3);
  return texture;
}

function tagTexture(): THREE.CanvasTexture {
  // 시각화용 패턴. 실기기에서 판독할 수 있는 등록 AprilTag 카드로 내보내지 않는다.
  const bits = [1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0];
  const texture = canvasTexture(512, (ctx) => {
    ctx.fillStyle = "#f3f2e9"; ctx.fillRect(0, 0, 512, 512);
    ctx.fillStyle = "#0d1113"; ctx.fillRect(56, 56, 400, 400);
    const cell = 50;
    ctx.fillStyle = "#faf9f1";
    bits.forEach((bit, i) => { if (bit) ctx.fillRect(106 + i % 6 * cell, 106 + Math.floor(i / 6) * cell, cell, cell); });
  });
  texture.magFilter = THREE.NearestFilter;
  return texture;
}

function mesh(geometry: THREE.BufferGeometry, material: THREE.Material, parent: THREE.Object3D, x = 0, y = 0, z = 0): THREE.Mesh {
  const object = new THREE.Mesh(geometry, material);
  object.position.set(x, y, z); object.castShadow = true; object.receiveShadow = true; parent.add(object);
  return object;
}

function box(parent: THREE.Object3D, size: [number, number, number], pos: [number, number, number], material: THREE.Material, radius = 0): THREE.Mesh {
  return mesh(radius ? new RoundedBoxGeometry(...size, 2, radius) : new THREE.BoxGeometry(...size), material, parent, ...pos);
}

function labelPlane(text: string, width: number, color = "#545c60", background?: string): THREE.Mesh {
  const texture = canvasTexture(512, (ctx, n) => {
    if (background) { ctx.fillStyle = background; ctx.fillRect(0, 0, n, n); }
    ctx.fillStyle = color; ctx.font = "700 65px 'Arial', sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(text, n / 2, n / 2, n - 28);
  });
  const object = new THREE.Mesh(new THREE.PlaneGeometry(width, width), new THREE.MeshStandardMaterial({ map: texture, transparent: !background, roughness: 0.8, depthWrite: Boolean(background) }));
  object.rotation.x = -Math.PI / 2;
  return object;
}

function buildDrone(scene: THREE.Scene): { drone: THREE.Group; rotors: THREE.Group[]; rotorBlur: THREE.Mesh[]; led: THREE.MeshStandardMaterial } {
  const drone = new THREE.Group(); scene.add(drone);
  const carbon = new THREE.MeshPhysicalMaterial({ map: carbonTexture(), color: 0xc1c6cb, roughness: 0.38, metalness: 0.48, clearcoat: 0.45, clearcoatRoughness: 0.27 });
  const black = new THREE.MeshStandardMaterial({ color: 0x13191d, roughness: 0.55, metalness: 0.25 });
  const aluminum = new THREE.MeshStandardMaterial({ color: 0x879195, roughness: 0.27, metalness: 0.85 });
  const copper = new THREE.MeshStandardMaterial({ color: 0xb77944, roughness: 0.3, metalness: 0.9 });
  const orange = new THREE.MeshStandardMaterial({ color: 0xe7a42b, roughness: 0.47, metalness: 0.05 });
  const rubber = new THREE.MeshStandardMaterial({ color: 0x252c2f, roughness: 0.97 });
  const pcb = new THREE.MeshStandardMaterial({ color: 0x1c5348, roughness: 0.55, metalness: 0.18 });
  const led = new THREE.MeshStandardMaterial({ color: 0x36f1d0, emissive: 0x22ddae, emissiveIntensity: 2.2, roughness: 0.22 });
  box(drone, [0.114, 0.009, 0.164], [0, 0.005, 0], carbon, 0.012);
  box(drone, [0.105, 0.007, 0.15], [0, -0.025, 0], carbon, 0.01);
  box(drone, [0.067, 0.014, 0.071], [0, -0.01, 0.016], pcb, 0.004);
  box(drone, [0.074, 0.044, 0.111], [0, 0.032, 0.006], black, 0.008);
  for (const z of [-0.03, 0.043]) box(drone, [0.081, 0.006, 0.016], [0, 0.056, z], orange, 0.003);
  const batteryLabel = labelPlane("4S · 850", 0.058, "#dfded6", "#252d31"); batteryLabel.position.set(0, 0.058, 0.007); drone.add(batteryLabel);
  for (const x of [-0.039, 0.039]) for (const z of [-0.054, 0.054]) {
    mesh(new THREE.CylinderGeometry(0.004, 0.004, 0.026, 10), aluminum, drone, x, -0.01, z);
    const screw = mesh(new THREE.CylinderGeometry(0.0046, 0.0046, 0.002, 6), aluminum, drone, x, 0.011, z); screw.rotation.y = Math.PI / 6;
  }
  const rotors: THREE.Group[] = []; const rotorBlur: THREE.Mesh[] = [];
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    const x = sx * 0.115; const z = sz * 0.112;
    const arm = box(drone, [0.027, 0.010, Math.hypot(x, z)], [x / 2, -0.004, z / 2], carbon, 0.004);
    arm.rotation.y = Math.atan2(x, z);
    mesh(new THREE.CylinderGeometry(0.0175, 0.019, 0.024, 24), black, drone, x, 0.01, z);
    mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.013, 24), copper, drone, x, 0.013, z);
    for (const dy of [0.004, 0.011, 0.020]) mesh(new THREE.TorusGeometry(0.0175, 0.0014, 6, 24), aluminum, drone, x, dy, z).rotation.x = Math.PI / 2;
    const rotor = new THREE.Group(); rotor.position.set(x, 0.027, z); drone.add(rotor); rotors.push(rotor);
    const bladeShape = new THREE.Shape(); bladeShape.moveTo(0.002, -0.004); bladeShape.bezierCurveTo(0.025, -0.015, 0.059, -0.010, 0.066, 0.002); bladeShape.bezierCurveTo(0.057, 0.009, 0.025, 0.012, 0.002, 0.004); bladeShape.closePath();
    const bladeGeometry = new THREE.ExtrudeGeometry(bladeShape, { depth: 0.0012, bevelEnabled: false });
    const bladeMaterial = new THREE.MeshPhysicalMaterial({ color: sz < 0 ? 0x5c7881 : 0x232e35, metalness: 0.12, roughness: 0.32, transparent: true, opacity: 0.84, side: THREE.DoubleSide });
    for (let i = 0; i < 3; i++) {
      const pivot = new THREE.Group(); pivot.rotation.y = i * Math.PI * 2 / 3; rotor.add(pivot);
      const blade = mesh(bladeGeometry, bladeMaterial, pivot); blade.rotation.x = -Math.PI / 2;
    }
    mesh(new THREE.CylinderGeometry(0.005, 0.006, 0.006, 12), aluminum, rotor, 0, 0.003, 0);
    const blur = mesh(new THREE.CircleGeometry(0.066, 48), new THREE.MeshBasicMaterial({ color: 0x86999e, transparent: true, opacity: 0.13, side: THREE.DoubleSide, depthWrite: false }), drone, x, 0.03, z);
    blur.rotation.x = -Math.PI / 2; blur.castShadow = false; rotorBlur.push(blur);
    const leg = mesh(new THREE.CylinderGeometry(0.003, 0.004, 0.052, 10), black, drone, sx * 0.048, -0.054, sz * 0.059);
    leg.rotation.z = -sx * 0.22;
    box(drone, [0.019, 0.007, 0.026], [sx * 0.053, -0.081, sz * 0.059], rubber, 0.004);
    mesh(new THREE.SphereGeometry(0.004, 10, 8), sz < 0 ? led : orange, drone, x * 0.68, -0.005, z * 0.68);
  }
  const cameraHousing = box(drone, [0.039, 0.032, 0.032], [0, -0.015, -0.088], black, 0.005);
  const lensRim = mesh(new THREE.CylinderGeometry(0.013, 0.013, 0.013, 24), aluminum, cameraHousing, 0, 0, -0.018); lensRim.rotation.x = Math.PI / 2;
  const glass = new THREE.MeshPhysicalMaterial({ color: 0x154259, metalness: 0.6, roughness: 0.08, clearcoat: 1, emissive: 0x073046, emissiveIntensity: 0.25 });
  mesh(new THREE.CylinderGeometry(0.010, 0.010, 0.002, 24), glass, cameraHousing, 0, 0, -0.025).rotation.x = Math.PI / 2;
  box(drone, [0.028, 0.009, 0.032], [0, -0.034, -0.025], pcb, 0.003);
  mesh(new THREE.CylinderGeometry(0.010, 0.012, 0.014, 20), glass, drone, 0, -0.046, -0.025);
  const antenna = mesh(new THREE.CylinderGeometry(0.0015, 0.0015, 0.058, 8), black, drone, 0.025, 0.045, 0.086); antenna.rotation.x = -0.4;
  mesh(new THREE.SphereGeometry(0.006, 10, 8), rubber, drone, 0.025, 0.073, 0.097);
  for (const [offset, color] of [[0, 0xb73d31], [0.006, 0x1c2327]]) {
    const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(-0.025 + offset, 0.036, 0.058), new THREE.Vector3(-0.038 + offset, 0.018, 0.086), new THREE.Vector3(-0.023 + offset, -0.012, 0.063)]);
    mesh(new THREE.TubeGeometry(curve, 12, 0.002, 6, false), new THREE.MeshStandardMaterial({ color, roughness: 0.75 }), drone);
  }
  return { drone, rotors, rotorBlur, led };
}

function buildHangar(scene: THREE.Scene): THREE.Group {
  const concrete = concreteTexture();
  const floor = mesh(new THREE.PlaneGeometry(18, 18), new THREE.MeshStandardMaterial({ color: 0xd0d1c7, map: concrete, roughness: 0.80, bumpMap: concrete, bumpScale: 0.009 }), scene);
  floor.rotation.x = -Math.PI / 2; floor.castShadow = false;
  const wallMat = new THREE.MeshStandardMaterial({ color: 0xa3b0b8, roughness: 0.87 });
  const steel = new THREE.MeshStandardMaterial({ color: 0x59646c, roughness: 0.48, metalness: 0.56 });
  const yellow = new THREE.MeshStandardMaterial({ color: 0xd4a635, roughness: 0.86, polygonOffset: true, polygonOffsetFactor: -1 });
  box(scene, [16, 5, 0.15], [0, 2.5, -5.3], wallMat);
  box(scene, [0.15, 5, 16], [-6.5, 2.5, 0], wallMat);
  const wallBase = new THREE.MeshStandardMaterial({ color: 0x697c87, roughness: 0.79 });
  box(scene, [16, 0.85, 0.025], [0, 0.425, -5.20], wallBase);
  box(scene, [0.025, 0.85, 16], [-6.40, 0.425, 0], wallBase);
  for (const x of [-6, -3, 0, 3, 6]) {
    box(scene, [0.13, 5, 0.16], [x, 2.5, -5.14], steel);
    box(scene, [2.7, 0.045, 0.025], [x + 1.5, 1.45, -5.16], steel);
    if (x < 6) {
      const windowMat = new THREE.MeshBasicMaterial({ color: 0xc8e4ec, toneMapped: false });
      box(scene, [2.45, 1.2, 0.02], [x + 1.5, 2.8, -5.18], windowMat);
      for (const dx of [-0.8, 0, 0.8]) box(scene, [0.027, 1.25, 0.055], [x + 1.5 + dx, 2.8, -5.12], steel);
      box(scene, [2.48, 0.025, 0.055], [x + 1.5, 2.8, -5.12], steel);
    }
  }
  for (const x of [-3.2, 3.2]) {
    box(scene, [1.35, 0.08, 0.42], [x, 3.9, -1.5], steel, 0.03);
    box(scene, [1.27, 0.015, 0.35], [x, 3.85, -1.5], new THREE.MeshBasicMaterial({ color: 0xfff5dc, toneMapped: false }));
  }
  // 지상 시험 구획: 실제 치수의 바닥 표시, 활주로처럼 과장된 격자 대신 조용한 모서리 표시.
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    box(scene, [0.48, 0.0015, 0.022], [sx * 1.10, 0.003, sz * 1.35], yellow);
    box(scene, [0.022, 0.0015, 0.48], [sx * 1.35, 0.003, sz * 1.10], yellow);
  }
  for (let i = 0; i < 15; i++) box(scene, [0.035, 0.001, i % 5 === 0 ? 0.10 : 0.04], [-0.7 + i * 0.1, 0.004, 0.95], steel);
  const bayLabel = labelPlane("BAY 04", 0.74, "#747b77"); bayLabel.position.set(-0.90, 0.006, 0.74); scene.add(bayLabel);
  const safeLabel = labelPlane("TEST AREA", 0.80, "#958443"); safeLabel.position.set(0, 0.006, -1.42); scene.add(safeLabel);
  const tagMat = new THREE.MeshStandardMaterial({ map: tagTexture(), roughness: 0.86, metalness: 0 });
  box(scene, [0.178, 0.005, 0.178], [0, 0.0025, 0], new THREE.MeshStandardMaterial({ color: 0x9f9e92, roughness: 0.9 }), 0.001);
  const tag = mesh(new THREE.PlaneGeometry(0.17, 0.17), tagMat, scene, 0, 0.0053, 0); tag.rotation.x = -Math.PI / 2; tag.castShadow = false;
  const tape = new THREE.MeshStandardMaterial({ color: 0xd3b870, roughness: 0.9 });
  for (const z of [-0.093, 0.093]) box(scene, [0.047, 0.0005, 0.017], [0, 0.0055, z], tape);
  const tagCover = new THREE.Group();
  const coverMaterial = new THREE.MeshStandardMaterial({ color: 0x333d40, roughness: 0.94, metalness: 0.04 });
  box(tagCover, [0.206, 0.006, 0.206], [0, 0.014, 0], coverMaterial, 0.002);
  box(tagCover, [0.040, 0.004, 0.045], [0, 0.014, 0.120], coverMaterial, 0.003);
  tagCover.visible = false; scene.add(tagCover);
  // 원경 소품은 시험 구획 밖에만 둔다.
  box(scene, [0.8, 0.055, 0.42], [-3.6, 0.86, -1.2], steel, 0.015);
  for (const x of [-3.93, -3.27]) for (const z of [-1.36, -1.04]) box(scene, [0.028, 0.84, 0.028], [x, 0.42, z], steel);
  box(scene, [0.32, 0.11, 0.23], [-3.65, 0.94, -1.2], new THREE.MeshStandardMaterial({ color: 0x343d41, metalness: 0.18, roughness: 0.7 }), 0.018);
  return tagCover;
}

function drawFallback(canvas: HTMLCanvasElement, props: SimulationSceneProps): void {
  const ctx = canvas.getContext("2d"); if (!ctx) return;
  const w = canvas.clientWidth; const h = canvas.clientHeight; const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#d5dcdb"; ctx.fillRect(0, 0, w, h);
  const range = Math.max(85, Math.abs(props.frame?.x ?? props.startXcm) + 25, Math.abs(props.frame?.y ?? props.startYcm) + 25);
  const scale = Math.min(w, h) / (range * 2);
  const X = (x: number) => w / 2 + x * scale; const Y = (y: number) => h / 2 - y * scale;
  ctx.strokeStyle = "rgba(63,80,86,.13)"; ctx.lineWidth = 1;
  for (let t = -range; t <= range; t += 10) { ctx.beginPath(); ctx.moveTo(X(t), 0); ctx.lineTo(X(t), h); ctx.moveTo(0, Y(t)); ctx.lineTo(w, Y(t)); ctx.stroke(); }
  ctx.fillStyle = "#f9faf5"; ctx.fillRect(X(-8.5), Y(8.5), 17 * scale, 17 * scale);
  ctx.fillStyle = "#19242a"; ctx.fillRect(X(-6), Y(6), 12 * scale, 12 * scale);
  ctx.fillStyle = "#f9faf5"; ctx.fillRect(X(-3), Y(3), 3 * scale, 3 * scale); ctx.fillRect(X(1), Y(-1), 3 * scale, 3 * scale);
  if (props.frame?.disturbance.tagOccluded) { ctx.fillStyle = "#333d40"; ctx.fillRect(X(-10.3), Y(10.3), 20.6 * scale, 20.6 * scale); }
  const x = props.frame?.x ?? props.startXcm; const y = props.frame?.y ?? props.startYcm;
  if (props.showGuides) {
    ctx.strokeStyle = "#078b8b"; ctx.lineWidth = 2; ctx.beginPath();
    props.frames.forEach((f, i) => i ? ctx.lineTo(X(f.x), Y(f.y)) : ctx.moveTo(X(f.x), Y(f.y))); ctx.stroke();
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(9,123,139,.6)"; ctx.beginPath(); ctx.arc(X(x), Y(y), props.heightCm * Math.tan(27.5 * Math.PI / 180) * scale, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.strokeStyle = "#27363d"; ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(X(x) - 19, Y(y) - 19); ctx.lineTo(X(x) + 19, Y(y) + 19); ctx.moveTo(X(x) + 19, Y(y) - 19); ctx.lineTo(X(x) - 19, Y(y) + 19); ctx.stroke();
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) { ctx.beginPath(); ctx.arc(X(x) + sx * 19, Y(y) + sy * 19, 11, 0, Math.PI * 2); ctx.stroke(); }
  ctx.fillStyle = "#d59734"; ctx.fillRect(X(x) - 7, Y(y) - 12, 14, 24);
  ctx.fillStyle = "#30414a"; ctx.font = "12px system-ui"; ctx.fillText(`높이 ${props.heightCm}cm · 좌표 ${x.toFixed(1)} / ${y.toFixed(1)}cm`, 18, h - 20);
}

export default function SimulationScene(props: SimulationSceneProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const fallbackRef = useRef<HTMLCanvasElement>(null);
  const propsRef = useRef(props); propsRef.current = props;
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    const host = hostRef.current; if (!host) return;
    let renderer: THREE.WebGLRenderer | undefined;
    let controls: OrbitControls | undefined;
    let scene: THREE.Scene | undefined;
    let environment: THREE.WebGLRenderTarget | undefined;
    let raf = 0; let failed = false; let disposed = false;
    let width = Math.max(1, host.clientWidth); let height = Math.max(1, host.clientHeight);
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let resetCamera = () => {};
    const activateFallback = () => { if (!disposed) { failed = true; setFallback(true); propsRef.current.onReady?.(false); } };
    const onContextLost = (event: Event) => { event.preventDefault(); activateFallback(); };
    const onContextRestored = () => { if (!disposed) { failed = false; setFallback(false); propsRef.current.onReady?.(true); } };
    let resize = () => {};
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75)); renderer.setSize(width, height, false);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 0.87;
      renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap; renderer.shadowMap.autoUpdate = false;
      renderer.domElement.setAttribute("aria-label", "3D 시험장: AprilTag와 드론의 실제 시뮬레이션 좌표");
      renderer.domElement.addEventListener("webglcontextlost", onContextLost);
      renderer.domElement.addEventListener("webglcontextrestored", onContextRestored);
      host.appendChild(renderer.domElement);
      scene = new THREE.Scene(); scene.background = new THREE.Color(0xb4c1c8); scene.fog = new THREE.Fog(0xb4c1c8, 8, 18);
      const pmrem = new THREE.PMREMGenerator(renderer); const room = new RoomEnvironment();
      environment = pmrem.fromScene(room, 0.04); scene.environment = environment.texture; scene.environmentIntensity = 0.4;
      room.dispose(); pmrem.dispose();
      scene.add(new THREE.HemisphereLight(0xe0efff, 0x666951, 0.95));
      const sun = new THREE.DirectionalLight(0xfff1d5, 2.8); sun.position.set(-2.6, 5.5, 2.5); sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -2.4; sun.shadow.camera.right = 2.4; sun.shadow.camera.top = 2.4; sun.shadow.camera.bottom = -2.4; sun.shadow.camera.near = 0.1; sun.shadow.camera.far = 12; sun.shadow.bias = -0.00012; sun.shadow.normalBias = 0.003; sun.shadow.radius = 3;
      scene.add(sun); scene.add(sun.target);
      const fill = new THREE.DirectionalLight(0xd8efff, 0.55); fill.position.set(3, 2.4, -2); scene.add(fill);
      const tagCover = buildHangar(scene);
      const { drone, rotors, rotorBlur, led } = buildDrone(scene);
      drone.position.copy(positionOf(propsRef.current));
      const orbit = new THREE.PerspectiveCamera(33, width / height, 0.015, 35);
      const top = new THREE.PerspectiveCamera(42, width / height, 0.015, 35); top.up.set(0, 0, -1);
      const down = new THREE.PerspectiveCamera(43, 4 / 3, 0.008, 20); down.up.set(0, 0, -1);
      controls = new OrbitControls(orbit, renderer.domElement);
      controls.enableDamping = !reducedMotion; controls.dampingFactor = 0.075; controls.minDistance = 0.5; controls.maxDistance = 7;
      controls.minPolarAngle = 0.08; controls.maxPolarAngle = Math.PI / 2 - 0.06; controls.enablePan = true; controls.zoomSpeed = 0.7;
      resetCamera = () => {
        const p = propsRef.current; const at = positionOf(p);
        const target = new THREE.Vector3(at.x * 0.35, p.heightCm / 200, at.z * 0.35);
        controls!.target.copy(target); orbit.position.copy(target).add(new THREE.Vector3(1.55, 0.95, 1.7));
        controls!.update(); top.position.set(at.x * 0.35, Math.max(2.8, p.heightCm / 100 + 1.5), at.z * 0.35 + 0.001); top.lookAt(at.x * 0.35, 0, at.z * 0.35);
      };
      resetCamera();
      const guides = new THREE.Group(); scene.add(guides);
      const footprint = new THREE.Group(); guides.add(footprint);
      const radius = props.heightCm / 100 * Math.tan(27.5 * Math.PI / 180);
      const footprintRing = mesh(new THREE.RingGeometry(radius - 0.004, radius, 80), new THREE.MeshBasicMaterial({ color: 0x169caa, transparent: true, opacity: 0.60, side: THREE.DoubleSide, depthWrite: false }), footprint);
      footprintRing.rotation.x = -Math.PI / 2; footprintRing.castShadow = false;
      const footprintFill = mesh(new THREE.CircleGeometry(radius, 80), new THREE.MeshBasicMaterial({ color: 0x20bbc2, transparent: true, opacity: 0.045, side: THREE.DoubleSide, depthWrite: false }), footprint);
      footprintFill.rotation.x = -Math.PI / 2; footprintFill.castShadow = false;
      const stopRing = mesh(new THREE.RingGeometry(0.048, 0.05, 48), new THREE.MeshBasicMaterial({ color: 0x0e9298, transparent: true, opacity: 0.75, side: THREE.DoubleSide, depthWrite: false }), guides, 0, 0.009, 0); stopRing.rotation.x = -Math.PI / 2; stopRing.castShadow = false;
      const trailGeo = new THREE.BufferGeometry();
      const trailPositions = new THREE.BufferAttribute(new Float32Array(650 * 3), 3); trailPositions.setUsage(THREE.DynamicDrawUsage);
      trailGeo.setAttribute("position", trailPositions); trailGeo.setDrawRange(0, 0);
      const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0x0394a9, transparent: true, opacity: 0.9 })); trail.frustumCulled = false; guides.add(trail);
      const heightGeo = new THREE.BufferGeometry();
      const heightPositions = new THREE.BufferAttribute(new Float32Array(6), 3); heightPositions.setUsage(THREE.DynamicDrawUsage);
      const heightDistances = new THREE.BufferAttribute(new Float32Array([0, props.heightCm / 100]), 1); heightDistances.setUsage(THREE.DynamicDrawUsage);
      heightGeo.setAttribute("position", heightPositions); heightGeo.setAttribute("lineDistance", heightDistances);
      const heightLine = new THREE.Line(heightGeo, new THREE.LineDashedMaterial({ color: 0x187e89, dashSize: 0.03, gapSize: 0.025, transparent: true, opacity: 0.45 })); heightLine.frustumCulled = false; guides.add(heightLine);
      resize = () => {
        width = Math.max(1, host.clientWidth); height = Math.max(1, host.clientHeight);
        renderer!.setSize(width, height, false); orbit.aspect = top.aspect = width / height; orbit.updateProjectionMatrix(); top.updateProjectionMatrix();
      };
      let lastFrame: SimFrame | null = null; let lastReset = props.resetKey; let lastHeight = props.heightCm; let lastStamp = 0; let blendStarted = 0;
      let lastTrailCount = -1; let lastTrailTime = -1;
      let previousPosition = drone.position.clone(); let targetPosition = drone.position.clone();
      let previousTilt = new THREE.Vector2(); let targetTilt = new THREE.Vector2();
      const tick = (stamp: number) => {
        if (disposed) return;
        raf = requestAnimationFrame(tick);
        const p = propsRef.current;
        if (failed) { if (fallbackRef.current) drawFallback(fallbackRef.current, p); return; }
        const changed = p.frame !== lastFrame || p.resetKey !== lastReset || p.heightCm !== lastHeight;
        if (changed) {
          const seek = !p.running || p.resetKey !== lastReset || (p.frame?.tMs ?? 0) < (lastFrame?.tMs ?? 0);
          previousPosition.copy(drone.position); targetPosition = positionOf(p); blendStarted = stamp;
          previousTilt.set(drone.rotation.x, drone.rotation.z);
          // 자세는 시각적 근사이며 위치·속도·제어기 계산에는 쓰지 않는다.
          targetTilt.set(clamp((p.frame?.out.pitchUs ?? 0) * 0.0014, -0.12, 0.12), clamp(-(p.frame?.out.rollUs ?? 0) * 0.0014, -0.12, 0.12));
          if (seek || reducedMotion) { previousPosition.copy(targetPosition); previousTilt.copy(targetTilt); }
          lastFrame = p.frame; lastHeight = p.heightCm;
          if (p.resetKey !== lastReset) { resetCamera(); lastReset = p.resetKey; }
        }
        const trailTime = p.frames[p.frames.length - 1]?.tMs ?? -1;
        if (changed || p.frames.length !== lastTrailCount || trailTime !== lastTrailTime) {
          const start = Math.max(0, p.frames.length - 650);
          for (let i = start; i < p.frames.length; i++) {
            const f = p.frames[i]; trailPositions.setXYZ(i - start, f.x / 100, 0.013, -f.y / 100);
          }
          trailPositions.needsUpdate = true; trailGeo.setDrawRange(0, p.frames.length - start);
          lastTrailCount = p.frames.length; lastTrailTime = trailTime;
        }
        const blend = reducedMotion ? 1 : clamp((stamp - blendStarted) / 100, 0, 1);
        drone.position.lerpVectors(previousPosition, targetPosition, blend);
        drone.rotation.x = THREE.MathUtils.lerp(previousTilt.x, targetTilt.x, blend); drone.rotation.z = THREE.MathUtils.lerp(previousTilt.y, targetTilt.y, blend);
        const dt = lastStamp ? Math.min(0.05, (stamp - lastStamp) / 1000) : 0; lastStamp = stamp;
        rotors.forEach((rotor, i) => { if (p.running && !reducedMotion && !p.frame?.disturbance.kill) rotor.rotation.y += dt * (i % 2 ? 1 : -1) * 105; });
        rotorBlur.forEach((blur) => { blur.visible = p.running && !p.frame?.disturbance.kill; });
        led.emissive.setHex(p.frame?.disturbance.kill ? 0xff4934 : p.frame?.out.state === "HOLD" ? 0xe8ab32 : 0x22ddae);
        tagCover.visible = p.frame?.disturbance.tagOccluded === true;
        guides.visible = p.showGuides;
        footprint.position.set(drone.position.x, 0.008, drone.position.z);
        footprint.scale.setScalar(p.heightCm / props.heightCm);
        heightPositions.setXYZ(0, drone.position.x, 0.012, drone.position.z); heightPositions.setXYZ(1, drone.position.x, drone.position.y, drone.position.z); heightPositions.needsUpdate = true;
        heightDistances.setX(1, drone.position.y - 0.012); heightDistances.needsUpdate = true;
        controls!.enabled = p.view === "orbit"; if (controls!.enabled) controls!.update();
        const r = renderer!; const s = scene!;
        r.setScissorTest(false); r.setViewport(0, 0, width, height);
        down.position.set(drone.position.x, drone.position.y - 0.09, drone.position.z - 0.025); down.lookAt(down.position.x, 0, down.position.z);
        down.aspect = 4 / 3;
        down.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(55 / 2)) / down.aspect)); down.updateProjectionMatrix();
        try {
          r.shadowMap.needsUpdate = true;
          // 렌즈는 착륙발보다 아래에 있어 기체가 화면에 들어오지 않는다. 기체를 숨기지 않아 그림자는 유지한다.
          if (p.view === "camera") {
            guides.visible = false;
            // 센서 영상비 4:3을 보존한다. 넓은 UI로 늘리면 수직 시야가 달라져 태그가 잘린다.
            const sensorWidth = Math.min(width, height * 4 / 3); const sensorHeight = sensorWidth * 3 / 4;
            const left = Math.round((width - sensorWidth) / 2); const bottom = Math.round((height - sensorHeight) / 2);
            r.setClearColor(0x1b2a33); r.clear();
            r.setScissorTest(true); r.setScissor(left, bottom, Math.round(sensorWidth), Math.round(sensorHeight)); r.setViewport(left, bottom, Math.round(sensorWidth), Math.round(sensorHeight));
          }
          r.render(s, p.view === "top" ? top : p.view === "camera" ? down : orbit);
          if (p.view === "camera") { r.setScissorTest(false); r.setViewport(0, 0, width, height); }
          guides.visible = p.showGuides;
          if (p.view !== "camera" && width >= 440 && height >= 300) {
            const insetW = Math.min(218, Math.floor(width * 0.26)); const insetH = Math.round(insetW * 0.75); const margin = 18;
            r.setScissorTest(true); r.setScissor(width - insetW - margin, height - insetH - margin, insetW, insetH); r.setViewport(width - insetW - margin, height - insetH - margin, insetW, insetH);
            r.clearDepth(); guides.visible = false; r.render(s, down); guides.visible = p.showGuides;
            r.setScissorTest(false); r.setViewport(0, 0, width, height);
          }
        } catch { activateFallback(); }
      };
      raf = requestAnimationFrame(tick);
      setFallback(false); propsRef.current.onReady?.(true);
    } catch {
      activateFallback();
      const tick = () => { if (disposed) return; if (fallbackRef.current) drawFallback(fallbackRef.current, propsRef.current); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    }
    const observer = new ResizeObserver(() => resize()); observer.observe(host);
    return () => {
      disposed = true; cancelAnimationFrame(raf); observer.disconnect(); controls?.dispose();
      const geometries = new Set<THREE.BufferGeometry>(); const materials = new Set<THREE.Material>(); const textures = new Set<THREE.Texture>();
      scene?.traverse((object) => {
        const renderable = object as THREE.Mesh;
        if (renderable.geometry) geometries.add(renderable.geometry);
        if (renderable.material) for (const material of Array.isArray(renderable.material) ? renderable.material : [renderable.material]) {
          materials.add(material);
          for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
        }
      });
      geometries.forEach((geometry) => geometry.dispose()); materials.forEach((material) => material.dispose()); textures.forEach((texture) => texture.dispose()); environment?.dispose();
      if (renderer) {
        renderer.domElement.removeEventListener("webglcontextlost", onContextLost); renderer.domElement.removeEventListener("webglcontextrestored", onContextRestored);
        renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
      }
    };
  }, []);

  return <div className={`simulation-scene${fallback ? " is-fallback" : ""}${props.view === "camera" ? " is-camera" : ""}`}>
    <div className="simulation-scene__webgl" ref={hostRef} />
    <canvas ref={fallbackRef} className="simulation-scene__fallback" aria-label="3D 대신 동일한 시뮬레이션 좌표로 그린 평면도" />
    {fallback ? <div className="simulation-scene__fallback-note" role="status"><b>3D 사용 불가</b><span>동일한 시뮬레이션을 평면도로 표시합니다.</span></div>
      : <>
        <div className="simulation-scene__stamp"><i /> 시험장 04 <span>축척 1:1 · 시뮬레이션</span></div>
        {props.view !== "camera" && <div className="simulation-scene__inset"><span>하향 카메라 · 시각 모델<br /><b>{!props.frame ? "대기" : props.frame.disturbance.tagOccluded ? "태그 가림" : props.frame.tagVisible ? "태그 추적" : "시야 밖"}</b></span><i /></div>}
        {props.view === "orbit" && <div className="simulation-scene__hint">드래그 회전 <span>·</span> 휠 확대 <span>·</span> 우클릭 이동</div>}
        {props.view === "camera" && <div className="simulation-scene__crosshair" aria-hidden="true" />}
      </>}
  </div>;
}

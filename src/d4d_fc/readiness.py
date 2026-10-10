"""조사 결과를 이 프로젝트(EMP/재밍 자율비행) 요구사항과 대조한다.

Notion 설계: XIAO ESP32S3가 UART로 FC에 이동 명령을 주고, MTF-01P로 광류·거리를
측정하며, 조종기가 끊기면 자동 미션을 중단하고 수동 회수가 가능해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    level: str
    detail: str
    action: str = ""


def _setting(settings: dict, name: str):
    for s in settings.get("items", []):
        if s.get("name") == name:
            return s
    return None


def _val(settings: dict, name: str, default=None):
    s = _setting(settings, name)
    if not s:
        return default
    return s.get("value_name", s.get("value", default))


def evaluate(data: dict[str, Any]) -> list[Check]:
    out: list[Check] = []
    st = data.get("status", {}).get("inav_status", {})
    sensors = st.get("sensors", [])
    settings = data.get("settings", {})
    ports = data.get("hardware", {}).get("serial_ports", [])
    modes = data.get("modes", {})
    sw_names = {r.get("mode") for r in modes.get("configured_mode_switches", [])}

    def port_with(fn: str):
        return [p for p in ports if fn in p.get("functions", [])]

    def free_ports():
        return [p["port_label"] for p in ports if p.get("functions") == ["(없음)"]]

    # ---------------------------------------------- 기본 하드웨어
    out.append(Check("기체 형식", OK if _val(settings, "platform_type") == "MULTIROTOR" else WARN,
                     f"platform_type={_val(settings, 'platform_type')}, "
                     f"모터 {data.get('hardware', {}).get('motor_count')}개"))
    acc_ok = "가속도계" in sensors
    out.append(Check("자세 센서", OK if acc_ok else FAIL,
                     f"가속도계={_val(settings, 'acc_hardware')}, 기압계={_val(settings, 'baro_hardware')}, "
                     f"지자기={_val(settings, 'mag_hardware')}",
                     "" if acc_ok else "가속도계가 감지되지 않습니다."))

    # ---------------------------------------------- MTF-01P (광류 + 거리)
    of_hw = _val(settings, "opflow_hardware")
    rf_hw = _val(settings, "rangefinder_hardware")
    of_port = port_with("OPTICAL_FLOW")
    rf_port = port_with("RANGEFINDER")
    msp_sensor_ports = [
        p for p in port_with("MSP") if p.get("port_label") != "USB VCP"
    ]
    detected_rangefinder = "레인지파인더" in sensors
    detected_opflow = "광류(OPFLOW)" in sensors
    missing = []
    if of_hw in (None, "NONE"):
        missing.append(f"opflow_hardware={of_hw}")
    if rf_hw in (None, "NONE"):
        missing.append(f"rangefinder_hardware={rf_hw}")
    if of_hw == "MSP" and rf_hw == "MSP":
        # MTF-01P의 INAV 출력 모드는 한 UART의 일반 MSP 기능을 쓴다. 전용
        # OPTICAL_FLOW/RANGEFINDER 직렬 비트가 없는 것이 정상이다.
        if not msp_sensor_ports:
            missing.append("MSP UART 없음")
        if not detected_rangefinder:
            missing.append("레인지파인더 미감지")
        if not detected_opflow:
            missing.append("광류 미감지")
    else:
        if not of_port:
            missing.append("OPTICAL_FLOW 기능이 할당된 UART 없음")
        if not rf_port:
            missing.append("RANGEFINDER 기능이 할당된 UART 없음")
    out.append(Check(
        "MTF-01P 광류/거리 센서", FAIL if missing else OK,
        ", ".join(missing) if missing else (
            f"opflow={of_hw}, rangefinder={rf_hw}, "
            f"MSP UART={[p['port_label'] for p in msp_sensor_ports]}"
        ),
        "MTF-01P를 FC의 MSP UART에 연결하고 opflow_hardware=MSP, "
        "rangefinder_hardware=MSP로 설정한 뒤 FC 센서 감지를 확인하세요."
        if missing else ""))

    # ---------------------------------------------- GPS 없는 위치추정
    dr = _val(settings, "inav_allow_dead_reckoning")
    out.append(Check("추측항법(dead reckoning)", WARN if dr == "OFF" else OK,
                     f"inav_allow_dead_reckoning={dr}",
                     "GPS 없이 광류만으로 위치를 유지하려면 이 값과 inav_w_xy_flow_* 가중치 검토가 필요합니다."
                     if dr == "OFF" else ""))
    out.append(Check("광류 융합 가중치", OK,
                     f"inav_w_xy_flow_p={_val(settings, 'inav_w_xy_flow_p')}, "
                     f"inav_w_xy_flow_v={_val(settings, 'inav_w_xy_flow_v')}, "
                     f"opflow_scale={_val(settings, 'opflow_scale')} (모두 기본값)",
                     "실비행 후 보정 대상입니다."))
    out.append(Check("지면추종 한계", OK,
                     f"inav_max_surface_altitude={_val(settings, 'inav_max_surface_altitude')} cm",
                     "이 고도 위에서는 거리센서 기반 고도유지가 동작하지 않습니다."))

    # ---------------------------------------------- XIAO -> FC 명령 경로
    ovr = _val(settings, "msp_override_channels")
    has_ovr_mode = "MSP RC OVERRIDE" in sw_names
    msp_uarts = [p["port_label"] for p in port_with("MSP") if p["port_label"] != "USB VCP"]
    if ovr in (0, None) or not has_ovr_mode:
        out.append(Check(
            "XIAO -> FC 자동 제어 경로", FAIL,
            f"msp_override_channels={ovr}, MSP RC OVERRIDE 스위치 "
            f"{'설정됨' if has_ovr_mode else '없음'}, MSP 가능 UART={msp_uarts or '없음'}",
            "INAV에서 보드가 조종을 넘겨받는 방법은 MSP RC override 입니다. "
            "msp_override_channels 비트마스크로 보드가 덮어쓸 채널을 지정하고, "
            "MSP RC OVERRIDE 모드를 조종기 스위치에 할당해야 합니다. "
            "이 스위치가 Notion 설계의 '수동/자동 조종권 선택'에 해당합니다."))
    else:
        out.append(Check("XIAO -> FC 자동 제어 경로", OK,
                         f"msp_override_channels=0x{ovr:X}, MSP UART={msp_uarts}"))

    # ---------------------------------------------- Failsafe
    fs = data.get("hardware", {}).get("failsafe", {})
    proc = fs.get("procedure") or _val(settings, "failsafe_procedure")
    level = FAIL if str(proc).startswith("DROP") else OK
    out.append(Check(
        "Failsafe 동작", level, f"failsafe_procedure={proc}, delay={fs.get('delay_ds')}(0.1초 단위)",
        "DROP은 링크가 끊기면 모터를 즉시 정지시킵니다(추락). Notion 설계의 "
        "'자동 미션 중단 후 수동 회수 가능' 또는 '유효한 센서에 따라 유지/착륙'과 맞지 않습니다. "
        "광류·거리 센서를 붙인 뒤 LAND로 바꾸는 것을 검토하세요." if level == FAIL else ""))

    # ---------------------------------------------- 수신기
    rx_ok = "DISABLED_RC_LINK" not in st.get("arming", [])
    out.append(Check("수신기 링크", OK if rx_ok else WARN,
                     f"serialrx_provider={_val(settings, 'serialrx_provider')}, "
                     f"RX_SERIAL 포트={[p['port_label'] for p in port_with('RX_SERIAL')] or '없음'}, "
                     f"링크={'정상' if rx_ok else '신호 없음'}",
                     "" if rx_ok else "조종기를 켜고 수신기 전원을 확인하세요. 수동 회수 경로가 없으면 시험하면 안 됩니다."))

    # ---------------------------------------------- 무장 차단
    blockers = st.get("arm_blockers", [])
    out.append(Check("무장 가능 여부", OK if not blockers else WARN,
                     f"차단 사유: {', '.join(blockers) or '없음'}"))
    return out


def print_checks(checks: list[Check]) -> None:
    icon = {OK: "[OK]  ", WARN: "[주의]", FAIL: "[필요]"}
    for c in checks:
        print(f"{icon[c.level]} {c.name}: {c.detail}")
        if c.action:
            print(f"        -> {c.action}")

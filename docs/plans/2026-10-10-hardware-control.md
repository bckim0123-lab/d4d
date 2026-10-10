# SPIDER Hardware Control Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare and verify a reversible, disarmed ESP32-to-INAV roll/pitch control path from the SPIDER web console.

**Architecture:** The browser keeps ownership of the XIAO Nordic UART BLE connection, while the local-only FastAPI process owns short COM9 sessions for FC reads and one allowlisted RAM setting. Pure Python and TypeScript evaluators turn telemetry into explicit safety gates; no endpoint or UI control can arm, override throttle, or run motors.

**Tech Stack:** Python 3.12, FastAPI, pyserial/MSP v1+v2, React 19, TypeScript 5.7, Web Bluetooth NUS, unittest, Vitest, Vite.

---

### Task 1: Correct INAV 9.1 mode and MSP sensor interpretation

**Files:**
- Modify: `src/d4d_fc/codes.py`
- Modify: `src/d4d_fc/readiness.py`
- Modify: `tests/test_fc.py`

**Step 1: Write failing mode-ID regression tests**

- Add a test asserting permanent ID 50 is `MSP RC OVERRIDE`, 52 is `TURTLE`,
  61 is `MULTI FUNCTION`, 63 is `MIXER TRANSITION`, and 67 is `GIMBAL CENTER`.
- Add a synthetic mode-range test proving ID 50 satisfies the automatic-control path check.

**Step 2: Run tests and verify the expected failure**

Run:

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.test_fc.ModeTests tests.test_fc.ReadinessTests -v
```

Expected: failures show ID 50 decoded as `LOITER CHANGE` and the override mode reported missing.

**Step 3: Correct the INAV 9.1 box table**

- Update only IDs verified by the connected FC `BOXIDS`, GitHub documentation, and saved
  `msp_live.json`.
- Preserve unrelated known IDs.

**Step 4: Write failing MSP-sensor readiness tests**

Construct a synthetic inspection result with:

- `rangefinder_hardware=MSP`
- `opflow_hardware=MSP`
- UART4 function `MSP`
- detected sensors containing rangefinder and optical flow
- valid rangefinder and nonzero optical-flow quality

Assert that the MTF-01P check is `ok`. Add failure cases for missing sensor detection and
missing MSP UART.

**Step 5: Run and verify the expected failure**

Run the same focused test command. Expected: the valid MSP topology is still rejected for
lacking dedicated `OPTICAL_FLOW` and `RANGEFINDER` serial-function bits.

**Step 6: Implement topology-aware readiness**

Treat the current topology as ready when both sensor hardware settings are MSP, both sensors
are detected, and at least one non-USB MSP UART exists. Keep dedicated serial-function support
for other configurations.

**Step 7: Run focused and full FC tests**

```powershell
python -m unittest tests.test_fc -v
python -m unittest discover -s tests -v
```

Expected: all tests pass; stale fixture assertions are updated only where INAV 9.1 naming was
wrong.

### Task 2: Add a pure ground-control safety evaluator

**Files:**
- Create: `src/d4d_fc/control.py`
- Create: `tests/test_control.py`

**Step 1: Write failing evaluator tests**

Cover one behavior per test:

- accepts the known FC identity, disarmed state, motors at 1000, throttle below `min_check`,
  receiver link, MSP UART5, override mode ID 50, and mask 64
- blocks an armed FC
- blocks throttle at or above `min_check`
- blocks any motor above 1000
- blocks missing RC link
- blocks an unknown board or firmware
- blocks an unexpected current mask
- allows restoration to 64 under looser conditions because it removes roll/pitch authority

**Step 2: Verify RED**

```powershell
python -m unittest tests.test_control -v
```

Expected: import failure because `d4d_fc.control` does not exist.

**Step 3: Implement minimal dataclasses and evaluator**

Add:

- `Gate(name, passed, detail, action)`
- `ControlReadiness(can_apply_67, can_restore_64, gates)`
- `evaluate_ground_control(fc_data, current_mask, min_check)`

The evaluator must be deterministic and perform no I/O.

**Step 4: Verify GREEN and refactor**

Run `python -m unittest tests.test_control -v`, then the full suite.

### Task 3: Implement an allowlisted RAM-only override writer

**Files:**
- Create: `src/d4d_fc/control_write.py`
- Modify: `src/d4d_fc/codes.py`
- Modify: `tests/test_control.py`

**Step 1: Write failing writer tests with a fake FC**

Test:

- only setting `msp_override_channels` is accepted
- only values 64 and 67 are accepted
- applying 67 requires the exact confirmation and enabled-write flag
- applying 67 is refused when armed, throttle is high, or a motor is above 1000
- payload is `b"msp_override_channels\x00" + struct.pack("<I", 67)`
- the value is read back and must match
- EEPROM command 250 is never sent
- restoration to 64 succeeds and is also verified
- a JSON backup contains previous value and a CLI restore line

**Step 2: Verify RED**

```powershell
python -m unittest tests.test_control.ControlWriteTests -v
```

Expected: missing module or function failures.

**Step 3: Implement the minimal writer**

- Query status, RC, motors, mode ranges, identity, `min_check`, and current mask immediately
  before writing.
- Send only `MSP2_COMMON_SET_SETTING` for the allowlisted setting.
- Re-read through `MSP2_COMMON_SETTING_INFO`.
- Never send `MSP_EEPROM_WRITE`.
- Write the backup before mutation.

**Step 4: Verify GREEN and source-level safety invariants**

Add a test that scans `control_write.py` and rejects references to arming, raw RC, motor set,
reboot, reset, PID, mixer, or EEPROM write commands.

### Task 4: Expose local FC status and two-step RAM control API

**Files:**
- Create: `src/d4d_web/hardware.py`
- Modify: `src/d4d_web/api.py`
- Modify: `tests/test_web.py`
- Create: `hardware/profile.json`

**Step 1: Write failing API tests**

Using dependency-injected fake hardware, test:

- `GET /api/hardware/profile` returns the consolidated MicoAir/XIAO/MTF topology
- `GET /api/hardware/fc/status` returns identity, sensors, RC, motors, modes, mask, gates
- challenge is rejected unless confirmation is exactly `프로펠러 제거 / 디스아밍`
- writes are rejected unless `D4D_HARDWARE_WRITES=1`
- challenge token is short-lived and single-use
- apply rechecks live state rather than trusting challenge-time state
- target must be 64 or 67
- serial-port errors become a structured 409/503 response, not a server crash

**Step 2: Verify RED**

```powershell
python -m unittest tests.test_web.WebApiTests -v
```

Expected: 404 responses for hardware endpoints.

**Step 3: Implement the hardware service**

- Keep the port default at `COM9`, configurable by `D4D_FC_PORT`.
- Serialize COM access with a process lock.
- Store one-time challenge tokens only in memory with a short expiry.
- Bind no raw MSP request endpoint.
- Return no FC UID or BLE MAC in the public profile.

**Step 4: Implement endpoints**

- `GET /api/hardware/profile`
- `GET /api/hardware/fc/status`
- `POST /api/hardware/fc/override/challenge`
- `POST /api/hardware/fc/override/apply`

**Step 5: Verify API and full Python suite**

Run focused tests, then `python -m unittest discover -s tests -v`.

### Task 5: Add typed board telemetry and command allowlist

**Files:**
- Create: `web/src/hardware.ts`
- Create: `web/src/hardware.test.ts`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

**Step 1: Install the frontend test runner**

Add Vitest as a development dependency and a `test` script.

**Step 2: Write failing parser/reducer tests**

Test:

- line-fragment buffering across BLE notifications
- latest value retained per telemetry `type`
- malformed lines reported without discarding the next valid line
- `flow.connected:false` is not an MTF fault when FC reports the UART4 sensor healthy
- allowed commands serialize with one newline
- `motortest`, `fcmsp`, `voicecmd`, arbitrary input, and unknown commands are rejected
- `kill off` is marked confirmation-required

**Step 3: Verify RED**

```powershell
cd web
npm test -- --run
```

Expected: missing `hardware.ts` exports.

**Step 4: Implement minimal typed helpers**

Define NUS UUIDs, telemetry types, reducer, freshness tracking, command union, and serializer.

**Step 5: Verify GREEN**

Run frontend tests and `npm run build`.

### Task 6: Build the SPIDER live-control panel

**Files:**
- Create: `web/src/HardwareControlBay.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/hardware.test.ts`

**Step 1: Write failing state-model tests**

Cover UI-facing derived states:

- disconnected/stale/online
- ground-test-ready versus flight-locked
- FC and board roll/pitch comparison
- controls disabled when armed, stale, high throttle, or `fc_link:false`

**Step 2: Verify RED**

Run Vitest and observe failures for missing derivation functions.

**Step 3: Implement BLE connection ownership**

- Keep both NUS TX notification and RX write characteristics.
- Request `info` after connecting.
- Handle GATT disconnect and stale timestamps.
- Never reconnect silently after a user cancellation.

**Step 4: Implement telemetry cards and gates**

Show:

- XIAO build/camera/vision
- XIAO↔FC MSP counters and sent/applied RC values
- FC armed/blockers/battery/current/motors
- FC USB sensors/modes/mask
- ground-test and flight-lock reasons

**Step 5: Implement allowlisted controls**

Add buttons for:

- status
- MSP on/off
- mission reset
- kill
- confirmed kill off
- RAM mask 67 challenge/apply
- RAM mask 64 restore

Do not add free-form command input, arm, throttle, motor test, or raw MSP controls.

**Step 6: Integrate into the existing cockpit**

Replace the raw four-line Bluetooth bay with the control component while preserving the
existing map, planner, approval, responsive layout, and accessibility behavior.

**Step 7: Verify frontend tests and production build**

```powershell
cd web
npm test -- --run
npm run build
```

### Task 7: Update operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/PRD.md`
- Create: `docs/HARDWARE_CONTROL.md`

**Step 1: Document source precedence and current blockers**

Record that the 10:51 CLI file predates later mode and motor-stop changes, and that current
live reads are authoritative.

**Step 2: Document the first ground-control procedure**

Include:

1. remove propellers and secure the frame
2. lower transmitter throttle below 1100
3. leave ARM off
4. connect FC USB and XIAO BLE
5. verify motors 1000 and receiver link
6. apply 67 in RAM
7. switch ch6 to board control
8. move AprilTag and compare board/FC roll-pitch
9. switch ch6 to manual or send `msp off`
10. restore 64 and verify motors remained 1000

**Step 3: State the flight blockers**

No flight until ESC response, motor order/direction, `-21` upload, height source, camera axes,
idle, and kill are separately verified.

### Task 8: Fresh verification and controlled live dry run

**Files:**
- Update generated build output only through normal build commands
- Save read-only evidence under `out/control_live/`

**Step 1: Run all automated verification**

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
cd web
npm test -- --run
npm run build
```

**Step 2: Check lints for edited files**

Use the IDE lint reader and fix newly introduced errors.

**Step 3: Run read-only live status**

Query COM9 and BLE without any setting write. Save the status and verify:

- FC disarmed
- motors 1000
- UART4 MTF healthy
- UART5 board link healthy
- receiver linked
- throttle below 1100

If throttle remains high, stop before RAM apply and report the exact blocker.

**Step 4: Apply mask 67 in RAM only**

Proceed only if every gate passes. Verify the re-read value is 67 and no EEPROM command was
sent.

**Step 5: Verify control-channel propagation**

With ch6 in board-control position and a visible AprilTag, compare XIAO `msp.out` roll/pitch
to FC `MSP_RC`. Do not arm.

**Step 6: Verify recovery and restore**

- Move ch6 to manual and verify FC follows transmitter.
- Send `msp off` and verify neutral/fallback.
- Restore mask 64 in RAM and re-read.
- Verify motors were 1000 throughout.

**Step 7: Record evidence and remaining blockers**

Do not call the aircraft flight-ready. Report separately:

- ground control path result
- RAM restoration result
- ESC/motor result
- firmware `-21` and axis-verification status
- next safe hardware action

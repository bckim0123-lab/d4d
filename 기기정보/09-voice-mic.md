# 09. 마이크·음성 명령

> 상태: 코드 완료(펌웨어 `-18`), 배선 완료. **보드 업로드와 실기 시험은 아직 안 했다.**

## 구조

보드는 소리를 받아 녹음하고, 인식기가 알아들은 **키워드**를 받아 정해진 명령만 실행한다. 인식기는 PC든 보드든 같은 구조 위에 붙는다.

```mermaid
flowchart LR
    MIC["INMP441<br/>16kHz"] --> B["보드<br/>소리 크기 · 녹음"]
    B -- "rec → 녹음 전송 (ADPCM)" --> PC["PC 인식<br/>(예: Whisper, 한국어)"]
    B -. "나중에" .-> KWS["보드 키워드 모델<br/>(TFLite Micro 등)"]
    PC -- "voicecmd 키워드" --> T["음성 명령 표<br/>(허용된 명령만)"]
    KWS -- "voicecmd 키워드" --> T
    T --> CMD["보드 명령 실행"]
```

## 마이크 입력

| 항목 | 값 |
|---|---|
| 마이크 | INMP441 (I2S 디지털, 24비트) |
| 배선 | SCK → D3, WS → D4, SD → D5, VDD → 3V3, GND, L/R → GND ([03](03-wiring.md)) |
| 샘플링 | 16kHz (음성 인식 표준) |
| 처리 | 32비트 스테레오로 읽고 소리 있는 채널 자동 선택 → 직류 제거 → 24비트를 16비트로(이득 ×16) |
| 보고 | `mic` 메시지 1Hz: 평균·최대 dBFS. 신호가 전혀 없으면 `ok:false` |

## 녹음 (`rec <초>`)

- 0.2~3초를 녹음해 **IMA ADPCM**(4비트, 4:1)으로 줄이고, base64를 1000자씩 나눠 보낸다.
  ```
  MIC_BEGIN <rate> <samples> <adpcm_bytes> <b64_len>
  …base64 줄…
  MIC_END
  ```
- 2초 녹음은 64KB에서 16KB로 줄어 블루투스(약 7KB/s)로 약 3초면 보낼 수 있다.
- 압축 품질: PC 왕복 시험에서 SNR 32.2dB로, 음성 인식에 충분하다.

## 음성 명령 (`voicecmd <키워드>`)

`config.h`의 `VOICE_COMMANDS` 표에 있는 키워드만 실행한다. **모터 시험과 아밍은 음성으로 할 수 없다.** 펌웨어가 `motortest`·`voicecmd`를 걸러낸다.

| 키워드 | 말 | 실행 |
|---|---|---|
| `stop` | 정지 / 멈춰 | `motorstop`, `msp off` (모터 시험 중지 + 보드 조종 해제 → 조종기로) |
| `resume` | 다시 시작 | `msp on` |
| `status` | 상태 | `info` |
| `snap` | 사진 | `snap` |
| `mission_reset` | 미션 처음부터 | `mission reset` |

## PC 도구 (`tools/voice_tool.py`)

```bash
python3 tools/voice_tool.py level                               # 소리 크기 실시간
python3 tools/voice_tool.py rec --sec 2 --out a.wav             # 녹음 → WAV
python3 tools/voice_tool.py collect --keyword stop --count 20   # 학습 데이터 → dataset/stop/*.wav
python3 tools/voice_tool.py cmd status                          # 음성 명령 연결 시험
# 블루투스: 앞에 uv run --with bleak --with pyserial 를 붙이고 --ble
```

## 인식 방식 계획

| 방식 | 장점 | 준비할 것 |
|---|---|---|
| PC 인식 | 한국어 문장도 가능, 바로 만들 수 있음 | PC 연결 필요. 소리 크기로 말 시작을 감지해 자동 녹음하는 기능, Whisper 같은 인식기 연결 |
| 보드 인식 | PC 없이 동작 | 키워드별 녹음 데이터(`collect`), 작은 키워드 모델 학습. Espressif ESP-SR은 한국어를 지원하지 않음 |

## 다음 할 일

1. 펌웨어 `-18`을 업로드한다.
2. `level`로 소리 크기가 반응하는지 확인한다. `ok:false`면 배선을 점검한다.
3. `rec`로 녹음해 WAV를 들어 본다. 소리가 작으면 `MIC_GAIN`을 올린다.
4. `cmd status`로 음성 명령 연결을 확인한다.
5. 인식 방식을 정해 붙인다.

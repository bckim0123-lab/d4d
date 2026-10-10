#pragma once
// INMP441 I2S 마이크: 소리 크기 보고와 짧은 녹음(ADPCM 압축 → base64 줄로 전송)
// 음성 인식은 하지 않는다. 인식기(PC 또는 보드 모델)가 결과를 `voicecmd <키워드>`로 넘긴다.
#include <stddef.h>
#include <stdint.h>

typedef void (*MicEmitFn)(const char *line);
typedef bool (*MicEmitWaitFn)(const char *line, size_t n, uint32_t timeoutMs);

bool micBegin(MicEmitFn emit, MicEmitWaitFn emitWait);  // I2S 시작 + 마이크 태스크
bool micRequestRecord(uint32_t ms);                      // 녹음 요청 (진행 중이면 false)
bool micOk();                                            // 최근 1초에 신호가 들어왔나


#include <Arduino.h>

constexpr int RX_PIN = D1;  // 
constexpr int TX_PIN = D0;  // 

constexpr uint32_t BAUD = 115200;
constexpr uint8_t MSP_SET_RAW_RC = 200;

uint16_t rc[8] = {
  1500, 1500, 1000, 1500,
  1000, 1000, 1000, 1000
};

uint32_t lastSend = 0;
uint32_t lastStep = 0;
uint32_t packets = 0;
uint8_t stepIndex = 0;

void sendRC() {
  uint8_t payload[16];
  uint8_t checksum = 16 ^ MSP_SET_RAW_RC;

  for (int i = 0; i < 8; i++) {
    payload[i * 2] = rc[i] & 0xFF;
    payload[i * 2 + 1] = rc[i] >> 8;
  }

  Serial1.write('$');
  Serial1.write('M');
  Serial1.write('<');
  Serial1.write((uint8_t)16);
  Serial1.write(MSP_SET_RAW_RC);

  for (int i = 0; i < 16; i++) {
    Serial1.write(payload[i]);
    checksum ^= payload[i];
  }

  Serial1.write(checksum);
  packets++;
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(BAUD, SERIAL_8N1,
                RX_PIN, TX_PIN);

  Serial.println("MSP Override Test");
  Serial.println("Throttle fixed at 1000");
}

void loop() {
  uint32_t now = millis();

  if (now - lastStep >= 1000) {
    lastStep = now;

    // 스로틀은 건드리지 않고
    // Roll과 Pitch 입력만 번갈아 변경
    rc[0] = 1500;
    rc[1] = 1500;

    switch (stepIndex) {
      case 0: rc[0] = 1400; break;
      case 1: rc[0] = 1600; break;
      case 2: rc[1] = 1400; break;
      case 3: rc[1] = 1600; break;
    }

    stepIndex = (stepIndex + 1) % 4;

    Serial.print("Roll=");
    Serial.print(rc[0]);
    Serial.print(" Pitch=");
    Serial.println(rc[1]);
  }

  if (now - lastSend >= 50) {
    lastSend = now;
    sendRC();
  }
  static uint32_t lastQuery = 0;

  if (millis() - lastQuery >= 1000) {
    lastQuery = millis();
    requestMSPVersion();
  }

  checkFCResponse();
}


void requestMSPVersion() {
  const uint8_t cmd = 1;  // MSP_API_VERSION

  Serial1.write('$');
  Serial1.write('M');
  Serial1.write('<');
  Serial1.write((uint8_t)0);  // payload length
  Serial1.write(cmd);
  Serial1.write(cmd);  // XOR checksum
}

void checkFCResponse() {
  while (Serial1.available()) {
    uint8_t b = Serial1.read();

    if (b >= 32 && b <= 126) {
      Serial.write(b);
    } else {
      Serial.printf(" [%02X]", b);
    }
  }
}

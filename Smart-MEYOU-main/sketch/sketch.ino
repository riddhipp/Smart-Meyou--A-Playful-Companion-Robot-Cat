#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>
#include <Servo.h>
#include "heart_frames.h"
#include <cmath>

// --- HARDWARE PINS ---
#define SERVO_FL_PIN 5  
#define SERVO_FR_PIN 6
#define SERVO_BL_PIN 9  
#define SERVO_BR_PIN 10  
#define EYE_SERVO_PIN 11
#define NECK_PIN 3

#define IR_LEFT_PIN A0
#define IR_RIGHT_PIN A1
#define FLAME_PIN A2

// --- OBJECTS ---
Arduino_LED_Matrix matrix;
Servo myServo, neckServo;
Servo servoFL, servoFR, servoBL, servoBR;
float currentNeck = 120;       // Starting smooth value for the neck
int lastWrittenNeck = -1;     // Tracks last written angle to prevent jitter
int gaitCommand = 0; 
const float walkSpeed = 0.02; 
const int amplitude = 25; 
const int centerAngle = 90; 
const int SIT_DEPTH = 45; 
float smoothingFactor = 0.15;
const int MIN_ANGLE = 50, MAX_ANGLE = 130;
const float deadzone = 0.08;
float currentFL, currentFR, currentBL, currentBR;
static float timeTracker = 0;

const int FL_OFFSET = 10; const int FR_OFFSET = -10;
const int BL_OFFSET = -10; const int BR_OFFSET = 10;
const int baseFL = 80; const int baseFR = 100;
const int baseBL = 80; const int baseBR = 100;

// Wave/Dance State Variables
static float waveStartPoint = 0;
static bool isWaving = false;
static unsigned long lastUpdateTime = 0;

// --- LED ARRAYS ---
uint8_t clear[104] = {0}; 
uint8_t halt[104] = {0,0,0,0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,1,1,1,0,0,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,0,0,1,1,1,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0,0,0,0};
uint8_t left[104] = {0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,0,0,0,0,0,0,0,0, 0,0,0,0,1,1,1,1,1,1,0,0,0};
uint8_t right[104] = {0,0,0,1,1,1,1,1,0,0,0,0,0, 0,0,0,1,0,0,0,0,1,0,0,0,0, 0,0,0,1,0,0,0,0,1,0,0,0,0, 0,0,0,1,0,0,0,1,0,0,0,0,0, 0,0,0,1,1,1,1,0,0,0,0,0,0, 0,0,0,1,0,1,0,0,0,0,0,0,0, 0,0,0,1,0,0,1,0,0,0,0,0,0, 0,0,0,1,0,0,0,1,0,0,0,0,0};
uint8_t forward[104] = {0,0,0,1,1,1,1,1,1,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0, 0,0,0,1,1,1,1,0,0,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0, 0,0,0,1,0,0,0,0,0,0,0,0,0};
uint8_t backward[104] = {0,0,0,0,1,1,1,1,0,0,0,0,0, 0,0,0,0,1,0,0,0,1,0,0,0,0, 0,0,0,0,1,0,0,0,0,1,0,0,0, 0,0,0,0,1,0,0,0,1,0,0,0,0, 0,0,0,0,1,1,1,1,0,0,0,0,0, 0,0,0,0,1,0,0,0,1,0,0,0,0, 0,0,0,0,1,0,0,0,0,1,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0};
uint8_t dance[104] = {0,0,0,1,1,1,1,1,0,0,0,0,0, 0,0,0,1,0,0,0,0,1,0,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,0,1,0,0,0,0,1,0,0,0,0, 0,0,0,1,1,1,1,1,0,0,0,0,0};
uint8_t hi[104] = {0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,1,1,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0, 0,1,0,0,1,0,0,1,0,0,0,0,0};
uint8_t status[104] = {0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,1,0,0,0,0,0,0,0,0,0,0, 0,0,1,1,1,1,1,1,1,0,0,0,0, 0,0,0,0,0,0,0,0,0,1,0,0,0, 0,0,1,0,0,0,0,0,0,0,1,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,0,0,1,1,1,1,1,0,0,0,0};
uint8_t sit[104] = {0,0,0,0,1,1,1,1,1,0,0,0,0, 0,0,0,1,0,0,0,0,0,1,0,0,0, 0,0,1,0,0,0,0,0,0,0,0,0,0, 0,0,1,1,1,1,1,1,1,0,0,0,1, 0,0,0,0,0,0,0,0,0,1,0,0,1, 0,0,1,0,0,0,0,0,0,0,1,0,1, 0,0,0,1,0,0,0,0,0,1,0,0,1, 0,0,0,0,1,1,1,1,1,0,0,0,0};

// --- VARIABLES ---
unsigned long last_command_time = 0;
const unsigned long DISPLAY_DURATION = 3000;
int targetAngle = 60;

// Track last written integer angles to stop flooding servo pulse updates
int lastWrittenFL = -1, lastWrittenFR = -1, lastWrittenBL = -1, lastWrittenBR = -1;

// --- HANDLERS ---
void set_servo_angle(int angle) { if (angle >= 0 && angle <= 180) targetAngle = angle; }
void set_forward_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 1; matrix.draw(forward); last_command_time = millis(); } }
void set_backward_state(int state) { if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 2; matrix.draw(backward); last_command_time = millis(); } }
void set_left_state(int state) { if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 3; matrix.draw(left); last_command_time = millis(); } }
void set_right_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 4; matrix.draw(right); last_command_time = millis(); } }
void set_halt_state(int state) {  if(state) { matrix.draw(halt); gaitCommand = 0; last_command_time = millis(); } }
void set_hi_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 5; matrix.draw(hi); last_command_time = millis(); } }
void set_dance_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 6; matrix.draw(dance); last_command_time = millis(); } }
void set_status_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 8; matrix.draw(status); last_command_time = millis(); } }
void set_d1_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 7; matrix.draw(dance); last_command_time = millis(); } }
void set_s1_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 9; matrix.draw(sit); last_command_time = millis(); } }
void set_s2_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 10; matrix.draw(sit); last_command_time = millis(); } }
void set_s3_state(int state) {  if(state) { digitalWrite(LED4_G, LOW); gaitCommand = 11; matrix.draw(sit); last_command_time = millis(); } }

void setup() {
    pinMode(LED_BUILTIN, OUTPUT);
    pinMode(LED4_G, OUTPUT); 
    pinMode(LED3_R, OUTPUT);
    pinMode(IR_LEFT_PIN, INPUT);
    pinMode(IR_RIGHT_PIN, INPUT);
    pinMode(FLAME_PIN, INPUT);
    digitalWrite(LED4_G, HIGH);
    digitalWrite(LED3_R, HIGH);
    digitalWrite(LED_BUILTIN, HIGH);
    Serial.begin(9600);
    matrix.begin();
    matrix.setGrayscaleBits(1);
    
    myServo.attach(EYE_SERVO_PIN);
    myServo.write(targetAngle);
    neckServo.attach(NECK_PIN);

    servoFL.attach(SERVO_FL_PIN); servoFR.attach(SERVO_FR_PIN);
    servoBL.attach(SERVO_BL_PIN); servoBR.attach(SERVO_BR_PIN);
    
    Bridge.begin();
    Bridge.provide("set_servo_angle", (void(*)(int))set_servo_angle);
    Bridge.provide("set_forward_state", (void(*)(int))set_forward_state);
    Bridge.provide("set_backward_state", (void(*)(int))set_backward_state);
    Bridge.provide("set_left_state", (void(*)(int))set_left_state);
    Bridge.provide("set_right_state", (void(*)(int))set_right_state);
    Bridge.provide("set_halt_state", (void(*)(int))set_halt_state);
    Bridge.provide("set_hi_state", (void(*)(int))set_hi_state);
    Bridge.provide("set_dance_state", (void(*)(int))set_dance_state);
    Bridge.provide("set_status_state", (void(*)(int))set_status_state);
    Bridge.provide("set_d1_state", (void(*)(int))set_d1_state);
    Bridge.provide("set_s1_state", (void(*)(int))set_s1_state);
    Bridge.provide("set_s2_state", (void(*)(int))set_s2_state);
    Bridge.provide("set_s3_state", (void(*)(int))set_s3_state);
    // Register the bridge provider so Arduino can call it
    //Bridge.provide("update_sensors", handle_sensor_data);
}

// Helper to write to servos only when the target integer degree changes (Stops jitter)
void safeServoWrite(Servo &servo, float &currentVal, int &lastWrittenVal) {
    int targetInt = (int)round(currentVal);
    if (targetInt != lastWrittenVal) {
        servo.write(targetInt);
        lastWrittenVal = targetInt;
    }
}

void loop() {
    Bridge.update();
    myServo.write(targetAngle);
     
    if (last_command_time != 0 && millis() - last_command_time > DISPLAY_DURATION) {
        matrix.draw(clear);
        last_command_time = 0;
    }
    
    
    
    int flameVal = analogRead(FLAME_PIN);
    //Serial.print(leftIRVal);
    //Serial.print(",");
    //Serial.print(rightIRVal);
    //Serial.print(",");
    //Serial.println(flameVal);
    Bridge.call("update_sensors",  flameVal);
    
    // --- GAIT ENGINE ---
    unsigned long currentMillis = millis();
    float targetFL, targetFR, targetBL, targetBR;
    
    if (currentMillis - lastUpdateTime >= 10) {
      lastUpdateTime = currentMillis;
  
      if (gaitCommand != 0) { 
        timeTracker += (walkSpeed * 10);
        float rawA = sin(timeTracker); 
        float rawB = cos(timeTracker); 
        float powerA = (rawA > 0) ? (rawA * 0.4) : (rawA * 1.6);
        float powerB = (rawB > 0) ? (rawB * 0.4) : (rawB * 1.6);
        float waveDance = sin(timeTracker * 3.0); 
        float pA = (abs(powerA) < deadzone) ? 0 : powerA;
        float pB = (abs(powerB) < deadzone) ? 0 : powerB;
  
        // Keep your deadzone check, but filter the result to prevent sudden spikes
        static float smoothPA = 0;
        static float smoothPB = 0;
        
        float targetPA = (abs(powerA) < deadzone) ? 0 : powerA;
        float targetPB = (abs(powerB) < deadzone) ? 0 : powerB;
        
        smoothPA += (targetPA - smoothPA) * 0.3; // 0.3 controls how fast it blends the jump
        smoothPB += (targetPB - smoothPB) * 0.3;
        
        // Then use smoothPA and smoothPB in your case calculations instead of pA and pB
  
        smoothingFactor = (gaitCommand >= 7 && gaitCommand <= 11) ? 0.02 : 0.15;
        // --- DETERMINE TARGET NECK ANGLE BASED ON GAIT ---
        float targetNeck = 120; // Default center position

  
        switch(gaitCommand) {
          
          case 1: { // FORWARD (Now using your old working backward logic)
            targetBL = constrain(centerAngle + BL_OFFSET + (smoothPA * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetFR = constrain(centerAngle + FR_OFFSET - (smoothPA * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetBR = constrain(centerAngle + BR_OFFSET - (smoothPB * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetFL = constrain(centerAngle + FL_OFFSET + (smoothPB * amplitude), MIN_ANGLE, MAX_ANGLE); 
            break;
          }
          case 2: { // BACKWARD (Now using your old forward logic) 
            targetFL = constrain(centerAngle + FL_OFFSET + (smoothPA * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetBR = constrain(centerAngle + BR_OFFSET - (smoothPA * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetFR = constrain(centerAngle + FR_OFFSET - (smoothPB * amplitude), MIN_ANGLE, MAX_ANGLE); 
            targetBL = constrain(centerAngle + BL_OFFSET + (smoothPB * amplitude), MIN_ANGLE, MAX_ANGLE); 
            break;
          }
          
          case 3: { // LEFT TURN (Inverted to match new forward orientation)
            /*
            int turnOffset = 15; 
            targetBR = constrain(centerAngle - (pA * amplitude) + turnOffset, MIN_ANGLE, MAX_ANGLE); 
            targetFR = constrain(centerAngle - (pB * amplitude) - turnOffset, MIN_ANGLE, MAX_ANGLE); 
            targetBL = constrain(centerAngle + (pA * amplitude) - turnOffset, MIN_ANGLE, MAX_ANGLE); 
            targetFL = constrain(centerAngle + (pB * amplitude) + turnOffset, MIN_ANGLE, MAX_ANGLE); 
            */
            float turnPulse = sin(timeTracker) * amplitude;
            float pivotPulse = cos(timeTracker) * (amplitude * 0.5); 

            // Signs flipped to execute a clean spin to the left
            targetFL = constrain(centerAngle + FL_OFFSET + turnPulse, MIN_ANGLE, MAX_ANGLE);
            targetBR = constrain(centerAngle + BR_OFFSET + pivotPulse, MIN_ANGLE, MAX_ANGLE);
            
            targetFR = constrain(centerAngle + FR_OFFSET - turnPulse, MIN_ANGLE, MAX_ANGLE);
            targetBL = constrain(centerAngle + BL_OFFSET - pivotPulse, MIN_ANGLE, MAX_ANGLE);
            break;
          }
          
          case 4: { // RIGHT TURN (Inverted to match new forward orientation)
            float turnPulse = sin(timeTracker) * amplitude;
            float pivotPulse = cos(timeTracker) * (amplitude * 0.5); 

            // Swapped the signs to reverse the spin direction back to the right
            targetFL = constrain(centerAngle + FL_OFFSET - turnPulse, MIN_ANGLE, MAX_ANGLE);
            targetBR = constrain(centerAngle + BR_OFFSET + pivotPulse, MIN_ANGLE, MAX_ANGLE);
            
            targetFR = constrain(centerAngle + FR_OFFSET + turnPulse, MIN_ANGLE, MAX_ANGLE);
            targetBL = constrain(centerAngle + BL_OFFSET - pivotPulse, MIN_ANGLE, MAX_ANGLE);
            break;
          }
          
                  
          case 5: { // HI / WAVE (Switched to use BR servo instead of FL)
            if (!isWaving) { waveStartPoint = millis(); isWaving = true; }
            unsigned long elapsed = millis() - (unsigned long)waveStartPoint;
            
            if (elapsed < 300) { 
                targetBR = centerAngle ; 
                targetBL = centerAngle + BL_OFFSET; 
                targetNeck = 120; 
            } 
            else if (elapsed < 1300) { 
                targetBR = centerAngle  ; 
                targetBL = (centerAngle + BL_OFFSET + 100) + (sin((elapsed - 300) * 0.01) * 30); 
                // Smoothly transitions from 120 to 130 as the wave progresses
                targetNeck = 120 - ((float)(elapsed - 300) / 1000.0) * 20.0; 
            } 
            else if (elapsed < 1600) { 
                targetBR = centerAngle + BR_OFFSET; 
                targetBL = centerAngle + 15; 
                targetNeck = 110; 
            }
            else { 
                isWaving = false; 
                gaitCommand = 0; 
                targetNeck = 120; 
            }
            
            targetFL = centerAngle + FL_OFFSET; 
            targetFR = centerAngle + FR_OFFSET;
            break;
          }
          case 6: { // TEST MODE
            smoothingFactor = 0.3;
            if ((millis() / 6000) % 2 == 0) {
              targetFL = baseFL + (rawA * amplitude); targetFR = baseFR + 10 + (rawA * amplitude);
              targetBL = baseBL + (rawA * amplitude); targetBR = baseBR + (rawA * amplitude);
            } else {
              targetFL = baseFL + (rawA * amplitude); targetFR = baseFR + 10 - (rawA * amplitude); 
              targetBL = baseBL - (rawA * amplitude); targetBR = baseBR + (rawA * amplitude);
            }
            break;
          }
          case 7: { // DANCE
            targetFL = constrain(centerAngle + FL_OFFSET + (waveDance * 35), MIN_ANGLE, MAX_ANGLE);
            targetBL = constrain(centerAngle + BL_OFFSET - (waveDance * 35), MIN_ANGLE, MAX_ANGLE);
            targetFR = constrain(centerAngle + FR_OFFSET - (waveDance * 20), MIN_ANGLE, MAX_ANGLE);
            targetBR = constrain(centerAngle + BR_OFFSET + (waveDance * 35), MIN_ANGLE, MAX_ANGLE);
            break;
          }
          case 8: { // SIT
            targetFL = centerAngle + FL_OFFSET + 2*SIT_DEPTH; targetFR = centerAngle + FR_OFFSET - 2*SIT_DEPTH;
            targetBL = centerAngle + BL_OFFSET - 2*SIT_DEPTH; targetBR = centerAngle + BR_OFFSET + 2*SIT_DEPTH;
            break;
          }
          case 9: { // FRONT STAND, BACK SIT
            targetFL = centerAngle + FL_OFFSET; targetFR = centerAngle + FR_OFFSET + 10;
            targetBL = centerAngle + BL_OFFSET + 2*SIT_DEPTH; targetBR = centerAngle + BR_OFFSET - 2*SIT_DEPTH;
            break;
          }
          case 10: { // FRONT SIT, BACK Stand
            targetFL = centerAngle + FL_OFFSET - SIT_DEPTH ; targetFR = centerAngle + FR_OFFSET + 2*SIT_DEPTH ;
            targetBL = centerAngle + BL_OFFSET; targetBR = centerAngle + BR_OFFSET;
            break;
          }
          case 11: { // FRONT BWD SIT, BACK FWD SIT
            targetFL = centerAngle + FL_OFFSET - 2*SIT_DEPTH; targetFR = centerAngle + FR_OFFSET - 2*SIT_DEPTH;
            targetBL = centerAngle + BL_OFFSET + 2*SIT_DEPTH; targetBR = centerAngle + BR_OFFSET + 2*SIT_DEPTH;
            break;
          }
          default: {
            targetFL = centerAngle + FL_OFFSET; targetFR = centerAngle + FR_OFFSET + 20;
            targetBL = centerAngle + BL_OFFSET; targetBR = centerAngle + BR_OFFSET;
            break;
          }
        }
        
        // Limit maximum change per frame to kill sudden mechanical snaps
        // --- RATE LIMITER (Only applies to walking cases 1 & 2) ---
        /*
        if (gaitCommand == 1 || gaitCommand == 2 || gaitCommand == 7) {
            float maxLegStep = 17.5; 
            targetFL = constrain(targetFL, currentFL - maxLegStep, currentFL + maxLegStep);
            targetFR = constrain(targetFR, currentFR - maxLegStep, currentFR + maxLegStep);
            targetBL = constrain(targetBL, currentBL - maxLegStep, currentBL + maxLegStep);
            targetBR = constrain(targetBR, currentBR - maxLegStep, currentBR + maxLegStep);
        }
        // ---------------------------------------------------------
        */
        currentFL += (targetFL - currentFL) * smoothingFactor;
        currentFR += (targetFR - currentFR) * smoothingFactor;
        currentBL += (targetBL - currentBL) * smoothingFactor;
        currentBR += (targetBR - currentBR) * smoothingFactor;
        currentNeck += (targetNeck - currentNeck) * smoothingFactor;
  
        safeServoWrite(servoFL, currentFL, lastWrittenFL);
        safeServoWrite(servoFR, currentFR, lastWrittenFR);
        safeServoWrite(servoBL, currentBL, lastWrittenBL);
        safeServoWrite(servoBR, currentBR, lastWrittenBR);
        safeServoWrite(neckServo, currentNeck, lastWrittenNeck);
      } 
      else {
        // Keep home values as floats to match safeServoWrite requirements
        float homeFL = centerAngle + FL_OFFSET;
        float homeFR = centerAngle + FR_OFFSET + 20;
        float homeBL = centerAngle + BL_OFFSET;
        float homeBR = centerAngle + BR_OFFSET;
        float homeNeck = 120.0;

        // Snap current variables directly to home to stop floating-point drift
        currentFL = homeFL;
        currentFR = homeFR;
        currentBL = homeBL;
        currentBR = homeBR;
        currentNeck = homeNeck;

        // Safe write using floats
        safeServoWrite(servoFL, currentFL, lastWrittenFL);
        safeServoWrite(servoFR, currentFR, lastWrittenFR);
        safeServoWrite(servoBL, currentBL, lastWrittenBL);
        safeServoWrite(servoBR, currentBR, lastWrittenBR);
        safeServoWrite(neckServo, currentNeck, lastWrittenNeck);
      }
    }
}
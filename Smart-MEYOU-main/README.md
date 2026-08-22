# Smart-MEYOU: A Playful Companion Robot Cat
Meet Meyou, an interactive, edge-AI-powered companion cat designed to bring a touch of life, emotion, and safety monitoring to your living space! Built using Arduino UNO Q, Meyou features real-time camera feed, emotional state tracking, voice interaction, and a dedicated flame sensor safety system. 

Bricks Used:
  Object Detection - Pretrained available model
  Keyword Spotting - Custom model
  Arduino Cloud
  Cloud ASR
  Cloud LLM
  Image Detection - Pretrained available model
  Web UI
Added Google TTS to get audio of responses

Inputs from ESP32-CAM (network connected) and a flame sensor

UNO Q:
  Python code:
    Does AI object detection
    Keyword spotting listens to commands
    Arduino Cloud updates on cloud Dashboard
    Web UI dashboard shows live feed, buttons, status logs and cat animation with inputs and otputs
    Sets Reminders when "REMIND" is read by OCR
    Looks for object when "FIND OBJECT"is read by OCR
    "GAME"can randomly set Find the number or Choose Color game
    Sends discord notifications for all triggered responses
    Speaker speaks Meyou's feelings and responses
    Flame detection and alerts

  .INO code
    Reads Flame sensor values and sends to python side
    Depending on gait value, modes such as FORWARD, BACKWARD, LEFT, RIGHT, DANCE, SIT, KICK, HI, and STATUS are chosen

  Communication between both sides over RPC

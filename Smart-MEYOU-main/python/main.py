import os
import re
import cv2
import time
import random
import pygame
import requests
import threading
import subprocess
import numpy as np
import queue
from google.cloud import texttospeech
from fastapi.responses import StreamingResponse
from arduino.app_utils import App, Bridge
from arduino.app_bricks.cloud_llm import CloudLLM
from arduino.app_bricks.web_ui.web_ui import WebUI
from arduino.app_bricks.object_detection import ObjectDetection
from arduino.app_bricks.keyword_spotting import KeywordSpotting
from arduino.app_bricks.image_classification import ImageClassification
from arduino.app_bricks.cloud_asr import CloudASR, CloudProvider
from arduino.app_bricks.arduino_cloud import ArduinoCloud
from textblob import TextBlob
from rapidocr import RapidOCR

# Initialize the OCR engine
engine = RapidOCR()
ocr_queue = queue.Queue(maxsize=1)  # <--- Thread-safe queue for frames
latest_ocr_text = ""
ocr_lock = threading.Lock()
frame_counter = 0
ocr_game_active = False
current_game_type = None  # "number" or "color"
target_number = 8
target_color = "red"
last_checked_text = ""
game_start_time = 0
DELAY_THRESHOLD = 15.0
interaction_value = 0  # Tracks interaction score for color game
attempts_left = 3
game_cooldown_end = 0
target_search_object = None
last_reminder_text = ""
last_reminder_time = 0
last_search_object = ""
last_search_time = 0
COOLDOWN_SECONDS = 5.0
AVAILABLE_COLORS = ["Red", "Blue", "Green", "Yellow"]
current_target_color = "Red"

#              CONFIGURATION 
ESP32_URL = "URL_HERE"
current_servo_pos = 55
latest_frame = None
prev_frame = None
frame_lock = threading.Lock()
asr_active = False
asr_lock = threading.Lock()
audio_lock = threading.Lock()

latest_left_ir = 0
latest_right_ir = 0
latest_flame_val = 9999

asr_interaction_count = 0  # Tracks successful voice chats
is_resting = False         # Prevents interruption during rest
rest_start_time = 0
REST_DURATION = 10.0
is_robot_speaking = False

robot_emotion = 80  # Start happy (10 = Sad/Lonely, 100 = Super Happy)
#               STATE MACHINE CONSTANTS & VARIABLES 
STATE_MANUAL = 0
STATE_WANDER = 1
STATE_AVOID_OBSTACLE = 2
STATE_CHASE_BALL = 3
STATE_FIRE_DANGER = 4
STATE_RESTING = 5
current_state = STATE_MANUAL
state_start_time = time.time()
action_duration = 4.0  # Duration for current action
action_end_time = 0    # Expiration time for current action
object_detected_sent = False
last_sent_objs = [] 
last_notification_time = 0  
object_notification_lock = threading.Lock()
#              INITIALIZE BRICKS 
spotting = KeywordSpotting()
try:
    if hasattr(spotting, "start"):
        spotting.start()
except Exception as e:
    print(f"KeywordSpotting start error: {e}")

time.sleep(3)  # Shortened safe delay
last_gait_command = -1
robot_mode = "MANUAL"
last_command_time = time.time()
INACTIVITY_TIMEOUT = 30.0  # Seconds of no commands before auto-wandering starts
# 1. Credentials and Setup for text to speech
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/app/python/credentials/gen-lang-client.json"
client = texttospeech.TextToSpeechClient()

web_ui = WebUI()
detector = ObjectDetection(confidence=0.3)
image_classification = ImageClassification()

cloud = ArduinoCloud()
#cloud.start()

asr = CloudASR(provider=CloudProvider.GOOGLE_SPEECH, language="en")
llm = CloudLLM(
    model="google:gemini-3.1-flash-lite",
    api_key="API_KEY_HERE",
    system_prompt="You are a helpful and playful Arduino Uno Q girl cat robot and answers in 10 words."
).with_memory(max_messages=10)

# Register Cloud Properties
WEBHOOK_URL = "URL_HERE"
def send_discord_notification(message, image_frame=None):
    try:
        data = {"content": message}
        if image_frame is not None:
            success, encoded_image = cv2.imencode(".jpg", image_frame)
            if success:
                files = {"file": ("snapshot.jpg", encoded_image.tobytes(), "image/jpeg")}
                response = requests.post(WEBHOOK_URL, data=data, files=files, timeout=5)
                return
        requests.post(WEBHOOK_URL, data=data, timeout=5)
    except Exception as e:
        print(f"Discord Webhook Error: {e}")
def set_audio_volume():
    # Set the USB speaker volume (Card 2) to a loud, clear level 
    #os.system("amixer -c 2 set Speaker 130 > /dev/null 2>&1")
    os.system("amixer -D hw:Device_1 set Speaker 130 > /dev/null 2>&1")
    
    print("Volume set on Card 1.")
set_audio_volume()
#              CAMERA FEED THREAD  
def capture_thread():
    global latest_frame
    while True:
        try:
            # Added a tight timeout (1.0s) so it never hangs indefinitely if ESP32 lags
            resp = requests.get(f"{ESP32_URL}/capture", timeout=0.5, stream=True)
            if resp.status_code == 200:
                frame_array = np.frombuffer(resp.content, np.uint8)
                frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
                if frame is not None:
                    with frame_lock: 
                        latest_frame = frame.copy()
            else:
                time.sleep(0.05)
        except requests.exceptions.Timeout:
            time.sleep(0.05)
            web_ui.send_message('status_log', {'log': f'Camera stream error Retrying'})
        except Exception as e:
            # If ESP32 drops connection completely, wait a moment before trying again
            print(f"Camera Stream Warning: {e}")
            web_ui.send_message('status_log', {'log': f'Camera stream error: {str(e)}'})
            web_ui.send_message("hardware_update", {"component": "camera", "status": "ERROR", "ok": False})
            time.sleep(0.5)

@web_ui.app.get("/video_feed")
def video_feed():
    def gen():
        while True:
            frame_to_send = None
            with frame_lock:
                if latest_frame is not None:
                    frame_to_send = latest_frame.copy()
            if frame_to_send is not None:
                success, encoded = cv2.imencode('.jpg', frame_to_send, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                if success:
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + encoded.tobytes() + b'\r\n')
            # Control framerate (~20 FPS) 
            time.sleep(0.08)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")
#           EXECUTE GAIT 
def execute_gait_by_id(command_id):
    mapping = {
        0: ("set_halt_state", "No move"),
        1: ("set_forward_state", "Move on"),
        2: ("set_backward_state", "Move back"),
        3: ("set_left_state", "Turning L"),
        4: ("set_right_state", "Turning R"),
        5: ("set_hi_state", "meow"),
        6: ("set_dance_state", "DANCING"),
        7: ("set_d1_state", "Dancing dancing"),
        8: ("set_status_state", "sleepy"),
        9: ("set_s1_state", "up"),
        10: ("set_s2_state", "Move move"),
    }
    action, text_to_speak = mapping.get(command_id, ("set_halt_state", "Stop"))
    command_label = action.replace("set_", "").replace("_state", "").upper()
    safe_spoken = command_label if command_label else "COMMAND"
    safe_response = text_to_speak if text_to_speak else "Meow!"
    print(f"Syncing: Triggering {action}")
    web_ui.send_message("chat_update", {
        "spoken": safe_spoken,
        "response": safe_response
    })
    Bridge.call(action, 1)
    threading.Thread(target=play_text, args=(text_to_speak,), daemon=True).start()

#            PROCESS CLOUD STREAM 
def handle_robot_command(sid, data):
    action = data.get("action")
    print(f"Received command from {sid}: {action}")
    if action in ["forward", "backward", "left", "right", "stop", "hi", "sit", "dance", "status","meyou"]:
        if action=="status":
            web_ui.send_message("status_log", {"message": f"Robot executing: {action.upper()}"})
        elif action=="forward":
            execute_gait_by_id(1)
        elif action=="backward":
            execute_gait_by_id(2)
        elif action=="left":
            execute_gait_by_id(3)
        elif action=="right":
            execute_gait_by_id(4)
        elif action=="hi":
            execute_gait_by_id(5)
        elif action=="dance":
            execute_gait_by_id(6)
        elif action=="sit":
            execute_gait_by_id(8)
        elif action=="stop":
            execute_gait_by_id(0)
        elif action=="meyou":
            handle_status_wake()
    return {"status": "success", "action": action}

web_ui.on_message("robot_command", handle_robot_command)
    
    #                PLAYBACK 
def play_text(text_to_speak):
    global is_robot_speaking
    cat_audio_files = ["meow1.wav", "meow2.wav", "meow3.wav", "meow4.wav"]
    with audio_lock:
        is_robot_speaking = True  
        set_audio_volume()
        parts = re.split(r"\b(meow[!\.\s]*)\b", text_to_speak, flags=re.IGNORECASE)
        for part in parts:
            if not part:
                continue
            if "meow" in part.lower():
                selected_sound = random.choice(cat_audio_files)
                if os.path.exists(selected_sound):
                    try:
                        subprocess.run(["aplay", "-q", "-D", "plughw:CARD=Device_1,DEV=0", selected_sound], check=True)
                    except Exception as e:
                        print(f"Cat sound error: {e}")
            else:
                ssml_content = f"""<speak><prosody rate="medium" pitch="+25%">{part}</prosody></speak>"""
                input_text = texttospeech.SynthesisInput(ssml=ssml_content)
                voice = texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Wavenet-F")
                audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=22050, volume_gain_db=16.0)
                response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
                output_filename = "temp_speech.wav"
                with open(output_filename, "wb") as out:
                    out.write(response.audio_content)
                try:
                    subprocess.run(["aplay", "-q", "-D", "plughw:CARD=Device_1,DEV=0", output_filename], check=True)
                except Exception as e:
                    print(f"TTS audio error: {e}")
                    web_ui.send_message("status_update", {"status": "speaker_error", "message": f"speaker warning: {e}"})
                    web_ui.send_message("hardware_update", {"component": "speaker", "status": "ERROR", "ok": False})
        is_robot_speaking = False  
def update_system(action, command_id):
    global asr_active, last_gait_command, robot_mode, last_command_time
    print(f"Executing Voice/Keyword: {action} (ID: {command_id})")  
    last_gait_command = command_id
    last_command_time = time.time()
    if command_id in [1, 2, 3, 4, 5, 6, 7, 11]:
        update_emotion(10)
    try:
        cloud.gaitCommand = command_id  
    except Exception as e:
        print(f"Cloud sync error: {e}")  
    if command_id == 11:
        handle_status_wake()
    else:
        execute_gait_by_id(command_id)       
    if command_id != 11: asr_active = False
def change_state(new_state, text_announcement):
    global current_state, state_start_time
    current_state = new_state
    state_start_time = time.time()
    print(f"State Changed To: {new_state}")
    threading.Thread(target=play_text, args=(text_announcement,), daemon=True).start()

def handle_status_wake():
    global robot_mode
    robot_mode = "MANUAL"
    Bridge.call("set_s3_state", 1)
    global asr_active
    if asr_lock.acquire(blocking=False):
        try:
            if not asr_active:
                asr_active = True
                threading.Thread(target=process_cloud_stream, daemon=True).start()
            else:
                print("ASR Session already active, ignoring request.")
        finally:
            asr_lock.release()
def process_cloud_stream():
    global asr_active, asr_interaction_count, rest_start_time, current_state, is_robot_speaking
    try:
        while asr_active:
            if is_robot_speaking:
                time.sleep(0.1)
                continue
            with asr.transcribe_stream(duration=3.0) as events:
                for event in events:
                    if is_robot_speaking:
                        break  # Break out immediately if speech starts
                    if event.type == "text" and event.data:
                        spoken = event.data.strip()
                        if len(spoken) < 2:
                            continue
                        print(f"Speech: {spoken}")
                        update_emotion(15)
                        resp = llm.chat(spoken)
                        print(f"Response: {resp}")
                        web_ui.send_message("chat_update", {
                            "spoken": spoken.upper(), 
                            "response": resp
                        })
                        cloud.llmResponse = f"Q: {spoken}\nA: {resp}"
                        web_ui.send_message("chat_update", {"spoken": spoken, "response": resp})
                        threading.Thread(target=play_text, args=(resp,), daemon=True).start()
                        asr_interaction_count += 1
                        if asr_interaction_count >= 2:
                            asr_interaction_count = 0 
                            execute_gait_by_id(8)
                            update_emotion(-5)
                            sleepy_msg = "I am feeling tired from talking. Taking a nap, meow"
                            web_ui.send_message("chat_update", {"spoken": "ZZZ...", "response": sleepy_msg})
                            threading.Thread(target=play_text, args=(sleepy_msg,), daemon=True).start()
                            rest_start_time = time.time()
                            current_state = STATE_RESTING
                            return
    except Exception as e: 
        print(f"Speech Error: {e}")
        web_ui.send_message("status_update", {"status": "mic_error", "message": f"Mic error: {e}"})
        web_ui.send_message("hardware_update", {"component": "mic", "status": "ERROR", "ok": False})
    finally: 
        asr_active = False
        web_ui.send_message("status_update", {"status": "mic", "message": "Microphone stopped"})

def execute_robot_rest_mode():
    global robot_mode, is_resting
    is_resting = True
    robot_mode = "RESTING"
    # 1. Sit down / Relax gait (ID 8)
    execute_gait_by_id(8)
    update_emotion(-5) # Slightly tired
    # 2. Speak a sleepy message
    sleepy_msg = "I am feeling a bit tired from all this talking. Taking a short nap, meow..."
    # --- NEW: Update Web UI with sleep status ---
    web_ui.send_message("chat_update", {
        "spoken": "ZZZ...", 
        "response": sleepy_msg
    })
    web_ui.send_message("status_log", {"message": "MEYOU entering nap mode (3/3 ASR interactions reached)."})
    threading.Thread(target=play_text, args=(sleepy_msg,), daemon=True).start()
    # 3. Rest for 10 seconds (adjust duration as desired)
    time.sleep(10.0)
    # 4. Wake back up to manual/active mode
    print("🌅 Nap complete! Waking back up.")
    execute_gait_by_id(0) # Steady standing state
    wake_msg = "I'm awake and ready to chat again!"
    web_ui.send_message("chat_update", {
        "spoken": "WAKE UP", 
        "response": wake_msg
    })
    web_ui.send_message("status_log", {"message": "Robot woke up from nap. Resuming manual mode."})
    threading.Thread(target=play_text, args=(wake_msg,), daemon=True).start()
    robot_mode = "MANUAL"
    is_resting = False
#           IOT CLOUD DASHBOARD BUTTON CALLBACK 
def on_gait_command_changed(client, value):
    global last_gait_command, robot_mode, last_command_time
    command_id = int(value)
    print(f"Dashboard Button Clicked: {command_id}")
    robot_mode = "MANUAL" 
    last_gait_command = command_id
    last_command_time = time.time()
    if command_id in [1,2,3,4,5, 6, 7, 11]: # Hi or Dance commands
        update_emotion(30)
    if command_id == 11:
        handle_status_wake()
    else:
        execute_gait_by_id(command_id)

def on_llm_response_changed(client,value):
    #llm_response=value
    print(f"Cloud llmResponse changed to: {value}")
def on_tracking_label_changed(client, value):
    pass
    #print(f"Cloud changed tracking label to: {value}")
    #threading.Thread(target=reset_cloud_cmd, daemon=True).start()

def update_system_manual(action, command_id):
    global robot_mode, last_command_time
    if current_state == STATE_RESTING:  # <--- CHANGED FROM is_resting
        print("💤 Robot is currently resting. Ignoring command.")
        return
    robot_mode = "MANUAL"
    last_command_time = time.time() 
    update_system(action, command_id)  
def on_emotion_changed(client, value):
    global robot_emotion
    robot_emotion = int(value)
    print(f"Cloud dashboard updated emotion to: {robot_emotion}")
def on_mood_changed(client, value):
    print(f"Cloud dashboard updated mood to: {value}")

def update_emotion(amount):
    global robot_emotion
    robot_emotion = max(15, min(100, robot_emotion + amount))
    cloud.robotEmotion = robot_emotion
    if robot_emotion >= 85:
        mood = "Super Happy 😻"
        emoji = "😻"  # Heart-eyes cat
    elif robot_emotion >= 70:
        mood = "Happy 😸"
        emoji = "😸"  # Grinning cat
    elif robot_emotion >= 50:
        mood = "Playful 😺"
        emoji = "😺"  # Smiling cat
    elif robot_emotion >= 35:
        mood = "Neutral 🐱"
        emoji = "🐱"  # Cat face
    elif robot_emotion >= 20:
        mood = "Lonely 😿"
        emoji = "😿"  # Crying cat
    else:
        mood = "Depressed 🙀"
        emoji = "🙀"  # Weary cat
    cloud.robotMood = mood
    print(f"❤️ Emotion: {robot_emotion}% | 🧠 Mood State: {mood}")
    # Push updated emotion, mood, and cat emoji to the Web UI
    web_ui.send_message("emotion_update", {
        "emotion": robot_emotion, 
        "mood": mood,
        "emoji": emoji
    })
def ocr_worker_loop():
    # background thread for OCR with TextBlob correction and translation
    global latest_ocr_text, target_search_object, last_reminder_text, last_reminder_time, last_search_object, last_search_time
    while True:
        try:
            frame_to_process = ocr_queue.get(timeout=0.5)
            while not ocr_queue.empty():
                try:
                    frame_to_process = ocr_queue.get_nowait()
                    ocr_queue.task_done()
                except queue.Empty:
                    break
            # Execute OCR engine
            result = engine(frame_to_process)
            if result and hasattr(result, 'txts') and result.txts:
                combined_text = " | ".join(result.txts)    
                blob = TextBlob(combined_text)
                corrected_text = str(blob.correct())
                with ocr_lock:
                    latest_ocr_text = combined_text
                current_time = time.time()
                # Check for REMIND with deduplication/cooldown
                if "REMIND" in corrected_text.upper():
                    if corrected_text != last_reminder_text or (current_time - last_reminder_time) > COOLDOWN_SECONDS:
                        last_reminder_text = corrected_text
                        last_reminder_time = current_time
                        handle_ocr_reminder(corrected_text)

                # Check for FIND OBJECT triggers with deduplication/cooldown
                text_upper = corrected_text.upper()
                for trigger in ["FIND OBJECT", "LOOK FOR", "SEARCH FOR", "FIND"]:
                    if trigger in text_upper:
                        idx = text_upper.find(trigger) + len(trigger)
                        extracted_object = corrected_text[idx:].strip().lower()
                        extracted_object = extracted_object.replace(".", "").replace("|", "").strip()
                        # Trigger only if it's a new search object or the cooldown has passed
                        if extracted_object != last_search_object or (current_time - last_search_time) > COOLDOWN_SECONDS:
                            last_search_object = extracted_object
                            last_search_time = current_time
                            target_search_object = extracted_object
                            msg = f"Got it! I am looking for: {target_search_object}"
                            print(f"🎯 {msg}")
                            web_ui.send_message("status_log", {"message": f"Target Set: {target_search_object}"})
                            threading.Thread(target=play_text, args=(msg,), daemon=True).start()
                        break
                print(f"📖 OCR: '{combined_text}' | Corrected: '{corrected_text}'")
                web_ui.send_message("ocr_update", f"{combined_text} (Corrected: {corrected_text})")
                process_guessing_game(combined_text, frame=frame_to_process)
            ocr_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"OCR Error: {e}")
            web_ui.send_message("ocr_update", "Trying to read")
def delayed_reminder_task(delay_seconds, reminder_content):
    """Waits out the delay and fires the alert."""
    time.sleep(delay_seconds)
    alert_text = f"Reminder! You asked me to remind you to: {reminder_content}"
    print(f"🔔 {alert_text}")
    # Speak reminder
    threading.Thread(target=play_text, args=(alert_text,), daemon=True).start()
    # Web UI & Discord notifications (without image)
    web_ui.send_message("status_log", {"message": f"⏰ Reminder Triggered: {reminder_content}"})
    discord_msg = f"⏰ **Reminder Triggered:** {reminder_content}"
    threading.Thread(target=send_discord_notification, args=(discord_msg,), daemon=True).start()

def schedule_reminder(delay_seconds, reminder_content):
    """Spawns a background thread to run the reminder countdown."""
    threading.Thread(
        target=delayed_reminder_task, 
        args=(delay_seconds, reminder_content), 
        daemon=True
    ).start()

def handle_ocr_reminder(detected_text):
    """Main function called when OCR detects a reminder keyword."""
    delay_seconds, reminder_content = parse_reminder_text(detected_text)
    response_text = f"Got it! I will remind you to {reminder_content} in {delay_seconds} seconds."
    print(f"⏰ Reminder set for {delay_seconds}s: {reminder_content}")
    # Speak immediate confirmation
    threading.Thread(target=play_text, args=(response_text,), daemon=True).start()
    # Schedule the background task
    schedule_reminder(delay_seconds, reminder_content)
def parse_reminder_text(detected_text):
    # OCR text to extract the delay time in seconds and the clean reminder message
    text_upper = detected_text.upper()
    # Default to 60 seconds (1 minute) if no time is specified
    delay_seconds = 60  
    time_match = re.search(r"IN\s+(\d+)\s*(MIN|MINUTE|SEC|SECOND)", text_upper)
    if time_match:
        amount = int(time_match.group(1))
        unit = time_match.group(2)
        if "SEC" in unit:
            delay_seconds = amount
        elif "MIN" in unit:
            delay_seconds = amount * 60
    # Extract the core message
    reminder_content = detected_text
    for trigger in ["REMIND ME TO", "REMIND ME", "REMINDER"]:
        if trigger in text_upper:
            idx = text_upper.find(trigger) + len(trigger)
            reminder_content = detected_text[idx:].strip()
            break
    # Strip out the time portion from the text
    reminder_content = re.sub(r"in\s+\d+\s*(min|minute|sec|seconds)s?", "", reminder_content, flags=re.IGNORECASE).strip()
    if not reminder_content:
        reminder_content = "something important"
    return delay_seconds, reminder_content
def start_number_guessing_game():
    global ocr_game_active, current_game_type, target_number, last_checked_text, game_start_time, attempts_left
    ocr_game_active = True
    current_game_type = "number"
    attempts_left=3
    target_number = random.randint(0, 9)
    last_checked_text = ""
    game_start_time = time.time()
    game_msg = "Number guessing game started! Show me a number between 0 and 9 using OCR!"
    print(f"🎮 {game_msg}")
    web_ui.send_message("game_update", {"title": "🔢 Number Guessing Game", "message": f"Show target number", "status": "playing"})
    web_ui.send_message("status_log", {"message": f"OCR Number Guessing Game Active "})
    threading.Thread(target=play_text, args=("Let's play a number guessing game from zero to nine. Show me a number!",), daemon=True).start()
def start_color_game():
    global ocr_game_active, current_game_type, current_target_color, last_checked_text, game_start_time
    ocr_game_active = True
    current_game_type = "color"
    # Randomly pick a color target for this round
    current_target_color = random.choice(AVAILABLE_COLORS)
    last_checked_text = ""
    game_start_time = time.time()
    print(f"🎮 Color game started (Target: {current_target_color})")
    # Send the specific target color to the game banner
    web_ui.send_message("game_update", {
        "title": "🎨 Color Game Active", 
        "message": f"Show something {current_target_color.upper()} + word 'GAME'", 
        "status": "playing"
    })
    web_ui.send_message("status_log", {"message": f"OCR Color Game Active (Target: {current_target_color} + GAME)"})
    threading.Thread(target=play_text, args=(f"Show me something {current_target_color}, along with the word game!",), daemon=True).start()
def process_guessing_game(detected_text, frame=None):
    global ocr_game_active, current_game_type, target_number, last_checked_text, game_start_time, interaction_value, attempts_left, game_cooldown_end, current_time
    # 1. Always update current_time first
    current_time = time.time()
    # 2. Check cooldown FIRST before doing anything else
    if current_time < game_cooldown_end:
        if "GAME" in detected_text.upper() and detected_text != last_checked_text:
            last_checked_text = detected_text
            tired_message = "I'm tired! Let me rest for a moment."
            print("💤 Game triggered during cooldown: Robot is tired.")
            web_ui.send_message("game_update", {
                "title": "😴 Resting...", 
                "message": "I'm tired! Please wait a moment.", 
                "status": "cooldown"
            })
            web_ui.send_message("chat_update", {"spoken": "I'M TIRED", "response": tired_message})
            threading.Thread(target=play_text, args=(tired_message,), daemon=True).start()
        return
    # 3. Check if "GAME" text is detected in OCR to start a new game (if NOT in cooldown)
    if not ocr_game_active:
        if "GAME" in detected_text.upper():
            chosen_game = random.choice(["number", "color"])
            if chosen_game == "number":
                start_number_guessing_game()
            else:
                start_color_game()
        return
    # Avoid processing duplicate identical text triggers repeatedly
    if detected_text == last_checked_text:
        return
    
    is_delayed = (current_time - game_start_time) > DELAY_THRESHOLD
    game_start_time = current_time
    #  COLOR GAME LOGIC 
    if current_game_type == "color":
        if "GAME" in detected_text.upper() and frame is not None:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            color_matched = False
            pixel_count = 0
            if current_target_color.lower() == "red":
                lower1 = np.array([0, 50, 50])
                upper1 = np.array([10, 255, 255])
                lower2 = np.array([170, 50, 50])
                upper2 = np.array([180, 255, 255])
                mask = cv2.inRange(hsv, lower1, upper1) + cv2.inRange(hsv, lower2, upper2)
                pixel_count = cv2.countNonZero(mask)
                if pixel_count > 1000:
                    color_matched = True
            elif current_target_color.lower() == "blue":
                lower_blue = np.array([100, 50, 50])
                upper_blue = np.array([140, 255, 255])
                mask = cv2.inRange(hsv, lower_blue, upper_blue)
                pixel_count = cv2.countNonZero(mask)
                if pixel_count > 1000:
                    color_matched = True
            elif current_target_color.lower() == "green":
                lower_green = np.array([35, 50, 50])
                upper_green = np.array([85, 255, 255])
                mask = cv2.inRange(hsv, lower_green, upper_green)
                pixel_count = cv2.countNonZero(mask)
                if pixel_count > 1000:
                    color_matched = True
            elif current_target_color.lower() == "yellow":
                lower_yellow = np.array([20, 50, 50])
                upper_yellow = np.array([35, 255, 255])
                mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
                pixel_count = cv2.countNonZero(mask)
                if pixel_count > 1000:
                    color_matched = True
            if color_matched:
                interaction_value += 5
                if is_delayed:
                    response_text = f"You delayed answers, but awesome, that is {current_target_color}!"
                else:
                    response_text = f"Awesome, you showed something {current_target_color}!"
                print(f"🎨 Color game passed! Target: {current_target_color} | Interaction value: {interaction_value}")
                execute_gait_by_id(8)
                discord_msg = f"🏆 **Color Game Won!** Robot successfully found a **{current_target_color}** object!"
                threading.Thread(target=send_discord_notification, args=(discord_msg, frame), daemon=True).start()
                web_ui.send_message("game_update", {
                    "title": "🎉 Color Game Won!", 
                    "message": f"Correct! {current_target_color} object + GAME found!", 
                    "status": "win"
                })
                web_ui.send_message("chat_update", {"spoken": f"{current_target_color.upper()} FOUND", "response": response_text})
                web_ui.send_message("status_log", {"message": f"Color game success! Target: {current_target_color} | Value: {interaction_value}"})
                web_ui.send_message("interaction_update", {"interaction_value": interaction_value})
                update_emotion(25)
                execute_gait_by_id(7) 
                ocr_game_active = False
                last_checked_text = detected_text
                # Trigger cooldown when a game finishes successfully too (for resting)
                game_cooldown_end = time.time() + 10.0
                threading.Thread(target=play_text, args=(response_text,), daemon=True).start()
        return

    #  NUMBER GUESSING GAME LOGIC 
    if current_game_type == "number":
        found_digits = [int(char) for char in detected_text if char.isdigit()]
        if not found_digits:
            return    
        attempts_left -= 1
        interaction_value += 1
        update_interaction_ui(interaction_value)
        if target_number in found_digits:
            if is_delayed:
                response_text = f"You delayed answers, but correct, it's {target_number}!"
            else:
                response_text = f"Correct, it's {target_number}!"
                
            web_ui.send_message("game_update", {
                "title": "🎉 Number Game Won!", 
                "message": f"Correct! Found number {target_number}!", 
                "status": "win"
            })
            discord_msg = f"🎉 **Number Game Won!** User successfully guessed the target number: **{target_number}**."
            threading.Thread(target=send_discord_notification, args=(discord_msg, frame), daemon=True).start()
            update_emotion(20)
            execute_gait_by_id(6)
            ocr_game_active = False
            last_checked_text = detected_text
            game_cooldown_end = time.time() + 10.0
            threading.Thread(target=play_text, args=(response_text,), daemon=True).start()
            return
        if attempts_left <= 0:
            response_text = f"Game over! You ran out of tries. The number was {target_number}."
            print("🔢 Number game lost: Out of attempts.")
            web_ui.send_message("game_update", {
                "title": "❌ Game Over", 
                "message": f"Out of tries! Target was {target_number}.", 
                "status": "lose"
            })
            update_emotion(-10)
            discord_msg = f"❌ **Number Game Lost!** User ran out of tries. The target number was **{target_number}**."
            threading.Thread(target=send_discord_notification, args=(discord_msg, frame), daemon=True).start()
            ocr_game_active = False
            last_checked_text = detected_text
            game_cooldown_end = time.time() + 10.0
            threading.Thread(target=play_text, args=(response_text,), daemon=True).start()
            return
        guessed_num = found_digits[0]
        last_checked_text = detected_text
        print(f"🔢 User guessed: {guessed_num}  | Attempts left: {attempts_left}")
        if guessed_num > target_number:
            response_text = "Go lower!"
            web_ui.send_message("game_update", {
                "title": f"🔢 Try: {guessed_num} ({attempts_left} left)", 
                "message": "📉 Too high! Go LESS.", 
                "status": "playing"
            })
            update_emotion(5)
        elif guessed_num < target_number:
            response_text = "Go higher!"
            web_ui.send_message("game_update", {
                "title": f"🔢 Try: {guessed_num} ({attempts_left} left)", 
                "message": "📈 Too low! Go MORE.", 
                "status": "playing"
            })
            update_emotion(5)
        elif guessed_num == target_number:
            response_text = "CORRECT!"
            web_ui.send_message("game_update", {
                "title": f"YOU WON", 
                "message": "📈 Right, you won.", 
                "status": "played"
            })
            update_emotion(5)

        threading.Thread(target=play_text, args=(response_text,), daemon=True).start()
        return

detection_queue = queue.Queue(maxsize=1)

def background_detector_loop():
    """Runs AI detection and classification in a separate background thread to keep the video feed smooth."""
    global last_sent_objs, last_notification_time, target_search_object
    while True:
        try:
            frame_to_detect = detection_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            _, img_bytes = cv2.imencode('.jpg', frame_to_detect, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            det = detector.detect(img_bytes.tobytes(), image_type='jpg')
            out = image_classification.classify(img_bytes.tobytes(), image_type="jpg", confidence=0.0)
            combined_objs = []
            raw_detections = []
            if det and 'detection' in det:
                for d in det['detection']:
                    name = d.get('class_name')
                    conf = float(d.get('confidence', 0))
            if out and 'classification' in out:
                for obj_det in out['classification']:
                    name = obj_det.get('class_name')
                    conf = float(obj_det.get('confidence', 0))
                    if conf >= 0.35 and name:
                        combined_objs.append(f"{name}({conf:.2f})")
                        raw_detections.append({"class_name": name, "confidence": conf})                            
            # --- detect object
            if target_search_object:
                # Check if the target object is present in any raw detections
                matched = any(target_search_object in str(d.get('class_name', '')).lower() for d in raw_detections)
                if matched:
                    found_msg = f"Found the {target_search_object}!"
                    print(f"🎉 {found_msg}")
                    # Speak and log the success
                    threading.Thread(target=play_text, args=(found_msg,), daemon=True).start()
                    web_ui.send_message("status_log", {"message": found_msg})
                    discord_msg = f"🚨 **High-Confidence Object Detected (>=70%)!**\nObjects: {target_search_object}"
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    log_entry = f"[{timestamp}] Object Found: {target_search_object}\n"
                    with open("object_history.txt", "a", encoding="utf-8") as f:
                        f.write(log_entry)
                    threading.Thread(target=send_discord_notification, args=(discord_msg, frame_to_detect), daemon=True).start()
                    # Reset so it doesn't spam continuously
                    target_search_object = None
            if len(combined_objs) > 0:
                status_text = f"Objs: {', '.join(combined_objs)} | Servo: {int(current_servo_pos)}°"
            else:
                status_text = f"Objs: None | Servo: {int(current_servo_pos)}°"
            current_time = time.time()
            NOTIFICATION_COOL_DOWN = 20.0 
            valid_discord_detections = [
                d for d in raw_detections 
                if float(d.get('confidence', 0)) >= 70 and str(d.get('class_name')).lower() == "person"
            ]
            if valid_discord_detections:
                disc_classes = sorted([str(d.get('class_name')) for d in valid_discord_detections])
                disc_objs = [f"{d.get('class_name')}({float(d.get('confidence', 0)):.2f})" for d in valid_discord_detections]
                disc_objs_str = ", ".join(disc_objs)
                with object_notification_lock:
                    has_new_objects = any(cls not in last_sent_objs for cls in disc_classes)
                    cooldown_expired = (current_time - last_notification_time > NOTIFICATION_COOL_DOWN)
                    if cooldown_expired or has_new_objects:
                        last_sent_objs = disc_classes
                        last_notification_time = current_time
                        discord_msg = f"🚨 **High-Confidence Object Detected (>=70%)!**\nObjects: {disc_objs_str}"
                        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        log_entry = f"[{timestamp}] Objects Detected: {disc_objs_str}\n"
                        with open("object_history.txt", "a", encoding="utf-8") as f:
                            f.write(log_entry)
                        threading.Thread(target=send_discord_notification, args=(discord_msg, frame_to_detect), daemon=True).start()
            else:
                with object_notification_lock:
                    if time.time() - last_notification_time > 10:
                        last_sent_objs = []
            web_ui.send_message("label_update", {"tracking": status_text})
            cloud.tracking_label = status_text
        except Exception as e:
            print(f"Detection thread error: {e}")
        detection_queue.task_done()
def update_sensors( flame):
    global latest_left_ir, latest_right_ir, latest_flame_val
    latest_flame_val = flame
    # Thresholds
    OBSTACLE_THRESHOLD = 200
    FLAME_THRESHOLD = 100
    #print(latest_left_ir,latest_right_ir,latest_flame_val)
    # Push live status to Web UI dashboard
    try:
        web_ui.send_message("sensor_update", {
            "flame_active": latest_flame_val <= FLAME_THRESHOLD
        })
    except Exception:
        pass
def update_interaction_ui(value):
    """Sends the updated interaction score to the Web UI dashboard."""
    web_ui.send_message("interaction_update", {"interaction_value": value})
    print(f"📊 UI Updated: Interaction Value -> {value}")
def handle_status_keyword():
    """Triggered when the keyword 'STATUS' is spotted."""
    print("📋 Keyword 'STATUS' detected! Pushing health logs to Web UI.")
    # 1. Send system activity log
    web_ui.send_message("status_log", "Diagnostic requested: Keyword 'STATUS' spotted.")
    # 2. Push component statuses to the Web UI hardware grid
    web_ui.send_message("hardware_update", {"component": "camera", "status": "CONNECTED", "ok": True})
    web_ui.send_message("hardware_update", {"component": "mic", "status": "WORKING", "ok": True})
    web_ui.send_message("hardware_update", {"component": "speaker", "status": "READY", "ok": True})
    web_ui.send_message("hardware_update", {"component": "mcu", "status": "CONNECTED", "ok": True})
    web_ui.send_message("hardware_update", {"component": "servo", "status": f"ACTIVE ({int(current_servo_pos)}°)", "ok": True})
    # 3. Have the cat announce it verbally or textually
    threading.Thread(target=play_text, args=("System status check complete, meow!",), daemon=True).start()
# Register emotion meter to sync both ways
cloud.register("robotEmotion", value=80, on_write=on_emotion_changed, sync="MOST_RECENT_WINS")  
    # Register mood string property
cloud.register("robotMood", value="Happy", on_write=on_mood_changed, sync="MOST_RECENT_WINS")
# Register Cloud Properties with Callback Support
cloud.register("gaitCommand", value=0,on_write=on_gait_command_changed,sync="MOST_RECENT_WINS")
cloud.register("llmResponse", value="",on_write=on_llm_response_changed,sync="MOST_RECENT_WINS")
cloud.register("tracking_label", value="Initializing...",on_write=on_tracking_label_changed,sync="MOST_RECENT_WINS")
#cloud.start()

Bridge.provide("update_sensors", update_sensors)
#              KEYWORD REGISTRATION 
spotting.on_detect("FORWARD", lambda: update_system_manual("set_forward_state", 1))
spotting.on_detect("BACKWARD", lambda: update_system_manual("set_backward_state", 2))
spotting.on_detect("LEFT", lambda: update_system_manual("set_left_state", 3))
spotting.on_detect("RIGHT", lambda: update_system_manual("set_right_state", 4))
spotting.on_detect("STOP", lambda: update_system_manual("set_halt_state", 0))
spotting.on_detect("HI", lambda: update_system_manual("set_hi_state", 5))
spotting.on_detect("DANCE", lambda: update_system_manual("set_dance_state", 6))
spotting.on_detect("SIT", lambda: update_system_manual("set_dance_state", 8))
spotting.on_detect("MEYOU", handle_status_wake)
spotting.on_detect("STATUS", handle_status_keyword)

#             MAIN LOOP 
def user_loop():
    global current_servo_pos, prev_frame, last_gait_command, object_detected_sent, last_sent_objs, last_notification_time, latest_right_ir, latest_left_ir, latest_flame_val
    global action_end_time, current_state, avoidance_direction, previous_gait_before_avoidance, robot_mode, action_duration
    global wander_step,last_command_time, wander_substep,ocr_thread_running, latest_ocr_text, frame_counter,last_ocr_push_time  # <--- Add wander_step here if you want to track it globally
    #cloud.loop()
    frame_counter += 1
    current_time = time.time()
    OBSTACLE_THRESHOLD = 100
    FLAME_THRESHOLD = 100
    if current_state == STATE_RESTING:
        if current_time - rest_start_time >= REST_DURATION:
            print("🌅 Nap complete! Waking back up.")
            execute_gait_by_id(0) # Steady standing state
            wake_msg = "I'm awake and ready to chat again!"
            web_ui.send_message("chat_update", {"spoken": "WAKE UP", "response": wake_msg})
            web_ui.send_message("status_log", {"message": "Robot woke up from nap. Resuming manual mode."})
            threading.Thread(target=play_text, args=(wake_msg,), daemon=True).start()
            robot_mode = "MANUAL"
            current_state = STATE_MANUAL
        return  # Skips the rest of the loop while resting
    # --- STATE 4: FIRE DANGER (HIGHEST PRIORITY) ---
    if 70< latest_flame_val <= FLAME_THRESHOLD and current_state != STATE_FIRE_DANGER:
        print(f"🔥 FLAME DETECTED! Value: {latest_flame_val}. Emergency retreat initiated!")
        # Backup immediately (gait ID 2 for backward)
        update_system("set_backward_state", 2)
        action_end_time = current_time + 10.0  # Initial backup duration
        current_state = STATE_FIRE_DANGER
        # Web UI warnings
        web_ui.send_message("chat_update", {"FIREEEE": "WAKE UP", "response": "RUN!!!"})
        web_ui.send_message("status_log", {"message": f"🚨 EMERGENCY: Flame sensor triggered at value {latest_flame_val}"})
        # Audio & Discord Alert
        hazard_msg = "Warning! Fire hazard detected. Backing away immediately!"
        threading.Thread(target=play_text, args=(hazard_msg,), daemon=True).start()
        with frame_lock:
            flame_frame = latest_frame.copy() if latest_frame is not None else None
        discord_msg = f"🚨 **EMERGENCY ALERT: Flame Detected!**\nSensor Value: {latest_flame_val}"
        threading.Thread(target=send_discord_notification, args=(discord_msg, flame_frame), daemon=True).start()
        return

    # Handle completion or ongoing check of fire danger retreat
    elif current_state == STATE_FIRE_DANGER:
        # Check if the flame is gone OR the max retreat time has elapsed
        if latest_flame_val > FLAME_THRESHOLD or current_time >= action_end_time:
            print("Fire danger cleared or retreat complete. Halting.")
            web_ui.send_message("chat_update", {"spoken": "!!!", "response": "HALTING"})
            update_system("set_halt_state", 0)
            current_state = STATE_MANUAL
            robot_mode = "MANUAL"
        return  # Skip other states while handling the fire danger
    if robot_mode == "MANUAL" and current_state != STATE_AVOID_OBSTACLE:
        if current_time - last_command_time >= INACTIVITY_TIMEOUT:
            #chosen_mode = random.choice(["AUTO_WANDER", "SIT"])
            update_emotion(-20)
            if robot_emotion < 30:
                print("😢 Robot is feeling sad and lonely...")
                execute_gait_by_id(8) # Sit state
                web_ui.send_message("chat_update", {"spoken": "", "response": "Feeling lonely"})
                threading.Thread(target=play_text, args=(" no...",), daemon=True).start()
                last_command_time = current_time
            else:
                chosen_mode = random.choice(["AUTO_WANDER", "SIT"])
                if chosen_mode == "AUTO_WANDER":
                    print("⏳ Inactivity timeout! Switching to AUTO_WANDER mode.")
                    web_ui.send_message("chat_update", {"spoken": "", "response": "Wandering"})
                    robot_mode = "AUTO_WANDER"
                    action_end_time = 0
                else:
                    print("⏳ Inactivity timeout! Decided to SIT.")
                    web_ui.send_message("chat_update", {"spoken": "", "response": "Sitting"})
                    robot_mode = "MANUAL"
                    execute_gait_by_id(8)
                    last_command_time = current_time
            
    if 'avoidance_direction' not in globals():
        avoidance_direction = 0
    if 'previous_gait_before_avoidance' not in globals():
        previous_gait_before_avoidance = 1 
    if 'wander_step' not in globals():
        wander_step = 0
    # --- STATE 1: OBSTACLE AVOIDANCE (Highest Priority) ---
    # --- STATE 2: AUTONOMOUS WANDERING ---
    if robot_mode == "AUTO_WANDER" and current_state != STATE_AVOID_OBSTACLE:
        if current_time >= action_end_time:
            if 'wander_substep' not in globals():
                wander_substep = 0  
            if wander_substep == 0:
                choice = random.choice(["SIT_THEN_STRETCH", "STRETCH_ONLY"])
                if choice == "SIT_THEN_STRETCH":
                    chosen_action = 8
                    print("🤖 Wander Choice: Sitting down first (ID 8)")
                    action_duration = random.uniform(15.0, 16.0) # Sit duration
                    wander_substep = 1  # Go to stretch part 1 next
                else:
                    chosen_action = 9
                    print("🤖 Wander Sequence: Stretch part 1 (ID 9)")
                    action_duration = random.uniform(4.0, 5.0)
                    wander_substep = 2  # Go to stretch part 2 next
            elif wander_substep == 1:
                chosen_action = 9
                print("🤖 Wander Sequence (Post-Sit): Stretch part 1 (ID 9)")
                action_duration = random.uniform(4.0, 5.0)
                wander_substep = 2
            elif wander_substep == 2:
                chosen_action = 10
                print("🤖 Wander Sequence: Stretch part 2 (ID 10)")
                action_duration = random.uniform(4.0, 5.0)
                wander_substep = 3
            elif wander_substep == 3:
                chosen_action = 0
                print("🤖 Wander Sequence: Standing steady (ID 0)")
                action_duration = random.uniform(2, 4)
                wander_substep = 4
            elif wander_substep == 4:
                chosen_action = 1
                print("🤖 Wander Sequence: Walking forward (ID 1)")
                action_duration = random.uniform(20.0, 25.0)
                wander_substep = 0  # Reset cycle back to random choice
            execute_gait_by_id(chosen_action)
            action_end_time = current_time + action_duration
            current_state = STATE_WANDER
    frame = None
    if 'last_ocr_time' not in globals():
        last_ocr_time = 0
    with frame_lock: 
        if latest_frame is not None: frame = latest_frame.copy()
    if frame is not None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if prev_frame is None: prev_frame = gray
        #              TRACKING LOGIC 
        frame_delta = cv2.absdiff(prev_frame, gray)
        _, thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            if cv2.contourArea(largest) > 500:
                (x, y, w, h) = cv2.boundingRect(largest)
                target_pos = int(np.interp(x + (w/2), [0, 160, 320], [20, 55, 90]))
                #current_servo_pos = int(current_servo_pos + ALPHA * (target_pos - current_servo_pos))
                current_servo_pos= target_pos
                Bridge.call("set_servo_angle", current_servo_pos)
        else:
            current_servo_pos = int(current_servo_pos + 0.8 * (55 - current_servo_pos))
            Bridge.call("set_servo_angle", current_servo_pos)
        if 'last_ocr_push_time' not in globals():
            last_ocr_push_time = 0
        if current_time - last_ocr_push_time >= 2.0:  # Only check text every 2 seconds
            last_ocr_push_time = current_time
            if ocr_queue.empty():
                try:
                    ocr_queue.put_nowait(frame.copy())
                except queue.Full:
                    pass
        #               AI DETECTION 
        status_text = f"Obj: None | Servo: {int(current_servo_pos)}°"
        
        if frame_counter % 5 == 0:
            if detection_queue.empty():
                try:
                    detection_queue.put_nowait(frame.copy())
                except queue.Full:
                    pass
        prev_frame = gray
    time.sleep(0.05)
if __name__ == "__main__":
    threading.Thread(target=capture_thread, daemon=True).start()
    threading.Thread(target=ocr_worker_loop, daemon=True).start()
    threading.Thread(target=background_detector_loop, daemon=True).start()
    try:
        App.start_brick(cloud)
    except Exception:
        pass
    App.run(user_loop=user_loop)
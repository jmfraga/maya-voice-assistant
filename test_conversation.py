#!/usr/bin/env python3
"""Quick test: record via BT mic → STT → Claude → TTS → BT speaker.
No wake word, no display. Press Enter to talk, Ctrl+C to exit."""

import os
import sys
import yaml
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
log = logging.getLogger("test")

with open("config.yaml") as f:
    config = yaml.safe_load(f)

from audio import bt_connect, record_until_silence, save_wav, play_audio, generate_sounds
from stt import STT
from tts import TTS
from llm import LLM
from db import Database

# Init
audio_cfg = config.get("audio", {})
bt_connect(audio_cfg.get("bt_device_mac", ""))

if not os.path.isfile("sounds/ready.wav"):
    generate_sounds("sounds/")

db = Database("data/assistant.db")
stt = STT(config.get("stt", {}))
tts = TTS(config.get("tts", {}))
llm = LLM(config.get("llm", {}), config.get("assistant", {}))

# Greeting
print("\n=== Test de conversacion con Maya ===")
print("Presiona Enter, habla por el mic BT, espera respuesta.")
print("Ctrl+C para salir.\n")

greeting = tts.speak("Hola, soy Maya. Estoy lista para ayudarle.")
if greeting:
    play_audio(greeting)
    os.unlink(greeting)

while True:
    try:
        input(">> Presiona Enter para hablar...")
        play_audio("sounds/wake_ack.wav")

        print("   Grabando...")
        audio = record_until_silence(
            sample_rate=audio_cfg.get("sample_rate", 16000),
            silence_threshold=audio_cfg.get("silence_threshold", 500),
            silence_duration=audio_cfg.get("silence_duration", 1.5),
            max_seconds=audio_cfg.get("max_record_seconds", 30),
        )
        if audio is None:
            print("   No detecte voz.")
            continue

        wav = save_wav(audio, audio_cfg.get("sample_rate", 16000))
        print("   Transcribiendo...")
        text = stt.transcribe(wav)
        os.unlink(wav)

        if not text:
            print("   No pude transcribir.")
            continue

        print(f"   Tu: {text}")

        print("   Pensando...")
        response, actions = llm.chat(text, "Usuario", db)
        print(f"   Maya: {response}")

        if actions:
            print(f"   Acciones: {actions}")

        print("   Hablando...")
        audio_path = tts.speak(response)
        if audio_path:
            play_audio(audio_path)
            os.unlink(audio_path)

    except KeyboardInterrupt:
        print("\nAdios!")
        break
    except Exception as e:
        log.error("Error: %s", e, exc_info=True)

"""Audio module: BT auto-connect, recording with silence detection, playback."""

import subprocess
import tempfile
import time
import logging
import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger("maya.audio")


def bt_connect(mac: str) -> bool:
    """Attempt to connect BT device. Returns True if connected."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True, text=True, timeout=5,
        )
        if "Connected: yes" in result.stdout:
            log.info("BT %s ya conectado", mac)
            return True

        log.info("Conectando BT %s...", mac)
        subprocess.run(["bluetoothctl", "connect", mac], capture_output=True, timeout=10)
        time.sleep(2)

        result = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True, text=True, timeout=5,
        )
        connected = "Connected: yes" in result.stdout
        if connected:
            log.info("BT conectado")
        else:
            log.warning("BT no se pudo conectar")
        return connected
    except Exception as e:
        log.error("Error BT connect: %s", e)
        return False


def find_bt_device(mac: str) -> str | None:
    """Find the PipeWire/ALSA device name for a BT MAC address."""
    mac_under = mac.replace(":", "_")
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if mac_under in line:
                return line.split("\t")[1]
    except Exception as e:
        log.warning("Error buscando BT source: %s", e)
    return None


def get_input_device() -> int | None:
    """Get the default input device index, preferring BT."""
    try:
        info = sd.query_devices(kind="input")
        return info["index"] if info else None
    except Exception:
        return None


def record_until_silence(
    sample_rate: int = 16000,
    silence_threshold: float = 500,
    silence_duration: float = 1.5,
    max_seconds: float = 30,
    device: int | None = None,
) -> np.ndarray | None:
    """Record audio until silence is detected. Returns numpy array or None."""
    chunk_duration = 0.1  # 100ms chunks
    chunk_samples = int(sample_rate * chunk_duration)
    max_chunks = int(max_seconds / chunk_duration)

    frames = []
    silent_chunks = 0
    silent_chunks_needed = int(silence_duration / chunk_duration)
    has_speech = False
    speech_threshold_chunks = 3  # Need at least 3 non-silent chunks

    log.info("Grabando... (umbral silencio=%s, max=%ss)", silence_threshold, max_seconds)

    try:
        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=chunk_samples,
            device=device,
        )
        stream.start()

        for _ in range(max_chunks):
            data, overflowed = stream.read(chunk_samples)
            if overflowed:
                log.warning("Audio buffer overflow")

            frames.append(data.copy())
            rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))

            if rms < silence_threshold:
                silent_chunks += 1
                if has_speech and silent_chunks >= silent_chunks_needed:
                    log.info("Silencio detectado, fin de grabacion")
                    break
            else:
                silent_chunks = 0
                speech_threshold_chunks -= 1
                if speech_threshold_chunks <= 0:
                    has_speech = True

        stream.stop()
        stream.close()

        if not has_speech:
            log.info("No se detecto voz")
            return None

        audio = np.concatenate(frames, axis=0)
        log.info("Grabados %.1f segundos", len(audio) / sample_rate)
        return audio

    except Exception as e:
        log.error("Error grabando: %s", e)
        return None


def save_wav(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Save numpy audio to a temporary WAV file. Returns path."""
    path = tempfile.mktemp(suffix=".wav", prefix="maya_")
    sf.write(path, audio, sample_rate)
    return path


def play_audio(path: str):
    """Play audio file via pw-play (routes through PipeWire/BT)."""
    try:
        subprocess.run(["pw-play", path], timeout=30)
    except subprocess.TimeoutExpired:
        log.warning("Playback timeout")
    except Exception as e:
        log.error("Error reproduciendo: %s", e)


def generate_sounds(sounds_dir: str):
    """Generate notification sounds using numpy."""
    import os
    sr = 16000

    # Wake acknowledgment chime (~0.3s, pleasant ascending tone)
    t = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
    chime = (np.sin(2 * np.pi * 880 * t) * 0.3 +
             np.sin(2 * np.pi * 1320 * t) * 0.2)
    envelope = np.minimum(t / 0.02, 1.0) * np.minimum((0.3 - t) / 0.05, 1.0)
    chime = (chime * envelope * 32767).astype(np.int16)
    sf.write(os.path.join(sounds_dir, "wake_ack.wav"), chime, sr)

    # Error sound (~0.4s, descending)
    t = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
    freq = 440 - 200 * t / 0.4
    err = np.sin(2 * np.pi * freq * t) * 0.3
    envelope = np.minimum(t / 0.02, 1.0) * np.minimum((0.4 - t) / 0.05, 1.0)
    err = (err * envelope * 32767).astype(np.int16)
    sf.write(os.path.join(sounds_dir, "error.wav"), err, sr)

    # Ready sound (~0.5s, three ascending notes)
    notes = []
    for freq, dur in [(523, 0.15), (659, 0.15), (784, 0.2)]:
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        note = np.sin(2 * np.pi * freq * t) * 0.3
        env = np.minimum(t / 0.01, 1.0) * np.minimum((dur - t) / 0.03, 1.0)
        notes.append((note * env * 32767).astype(np.int16))
    ready = np.concatenate(notes)
    sf.write(os.path.join(sounds_dir, "ready.wav"), ready, sr)

    log.info("Sonidos generados en %s", sounds_dir)

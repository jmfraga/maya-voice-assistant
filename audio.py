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
    initial_wait: float = 0.0,
) -> np.ndarray | None:
    """Record audio until silence is detected. Uses pw-record for BT mic support.

    initial_wait: seconds to wait for speech before giving up (0 = no limit,
                  used for follow-up listening where user may not speak).
    """
    import io

    log.info("Grabando... (umbral silencio=%s, max=%ss, espera=%.1fs)",
             silence_threshold, max_seconds, initial_wait)

    try:
        # Use pw-record to capture from PipeWire (sees BT mic)
        proc = subprocess.Popen(
            ["pw-record", "--format=s16", "--rate=%d" % sample_rate,
             "--channels=1", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        chunk_duration = 0.1  # 100ms
        chunk_bytes = int(sample_rate * chunk_duration) * 2  # 16-bit = 2 bytes/sample
        max_chunks = int(max_seconds / chunk_duration)
        silent_chunks_needed = int(silence_duration / chunk_duration)
        initial_wait_chunks = int(initial_wait / chunk_duration) if initial_wait > 0 else 0

        frames = []
        silent_chunks = 0
        has_speech = False
        speech_threshold_chunks = 3
        total_chunks = 0

        for _ in range(max_chunks):
            raw = proc.stdout.read(chunk_bytes)
            if not raw or len(raw) < chunk_bytes:
                break

            total_chunks += 1
            data = np.frombuffer(raw, dtype=np.int16)
            frames.append(data.copy())
            rms = np.sqrt(np.mean(data.astype(np.float32) ** 2))

            if rms < silence_threshold:
                silent_chunks += 1
                # If waiting for follow-up and no speech after initial_wait, give up
                if initial_wait_chunks > 0 and not has_speech and total_chunks >= initial_wait_chunks:
                    log.info("Sin follow-up tras %.1fs", initial_wait)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return None
                if has_speech and silent_chunks >= silent_chunks_needed:
                    log.info("Silencio detectado, fin de grabacion")
                    break
            else:
                silent_chunks = 0
                speech_threshold_chunks -= 1
                if speech_threshold_chunks <= 0:
                    has_speech = True

        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

        if not has_speech:
            log.info("No se detecto voz")
            return None

        audio = np.concatenate(frames)
        # Reshape to (N, 1) for consistency with save_wav
        audio = audio.reshape(-1, 1)
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

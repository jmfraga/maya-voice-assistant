"""Speaker identification using resemblyzer voiceprints."""

import os
import logging
import numpy as np

log = logging.getLogger("maya.speaker_id")


class SpeakerID:
    def __init__(self, config: dict, users_config: dict, base_dir: str):
        self.enabled = config.get("enabled", True)
        self.threshold = config.get("similarity_threshold", 0.75)
        self.enrollment_samples = config.get("enrollment_samples", 5)
        self.users_config = users_config
        self.base_dir = base_dir
        self.encoder = None
        self.voiceprints = {}  # user_id -> embedding

        if self.enabled:
            self._load()

    def _load(self):
        """Load resemblyzer encoder and existing voiceprints."""
        try:
            from resemblyzer import VoiceEncoder
            self.encoder = VoiceEncoder()
            log.info("Resemblyzer encoder cargado")
        except Exception as e:
            log.error("Error cargando resemblyzer: %s", e)
            self.enabled = False
            return

        for user_id, user_cfg in self.users_config.items():
            vp_file = user_cfg.get("voiceprint_file", "")
            vp_path = os.path.join(self.base_dir, vp_file)
            if os.path.isfile(vp_path):
                self.voiceprints[user_id] = np.load(vp_path)
                log.info("Voiceprint cargado: %s", user_id)

    def identify(self, audio: np.ndarray, sample_rate: int = 16000) -> str | None:
        """Identify speaker from audio. Returns user_id or None."""
        if not self.enabled or not self.encoder or not self.voiceprints:
            return None

        try:
            from resemblyzer import preprocess_wav
            # resemblyzer expects float32 normalized audio
            if audio.dtype == np.int16:
                audio_f = audio.astype(np.float32) / 32768.0
            else:
                audio_f = audio.astype(np.float32)

            if audio_f.ndim > 1:
                audio_f = audio_f[:, 0]

            wav = preprocess_wav(audio_f, source_sr=sample_rate)
            embedding = self.encoder.embed_utterance(wav)

            best_user = None
            best_sim = 0.0

            for user_id, vp in self.voiceprints.items():
                sim = np.dot(embedding, vp) / (
                    np.linalg.norm(embedding) * np.linalg.norm(vp)
                )
                if sim > best_sim:
                    best_sim = sim
                    best_user = user_id

            if best_sim >= self.threshold:
                log.info("Hablante identificado: %s (sim=%.2f)", best_user, best_sim)
                return best_user
            else:
                log.info("Hablante no reconocido (mejor sim=%.2f < %.2f)",
                         best_sim, self.threshold)
                return None

        except Exception as e:
            log.error("Error identificando hablante: %s", e)
            return None

    def enroll(self, user_id: str, audio_samples: list[np.ndarray],
               sample_rate: int = 16000) -> bool:
        """Enroll a user with multiple audio samples."""
        if not self.encoder:
            log.error("Encoder no disponible para enrollment")
            return False

        try:
            from resemblyzer import preprocess_wav
            embeddings = []

            for audio in audio_samples:
                if audio.dtype == np.int16:
                    audio_f = audio.astype(np.float32) / 32768.0
                else:
                    audio_f = audio.astype(np.float32)

                if audio_f.ndim > 1:
                    audio_f = audio_f[:, 0]

                wav = preprocess_wav(audio_f, source_sr=sample_rate)
                emb = self.encoder.embed_utterance(wav)
                embeddings.append(emb)

            # Average embedding
            avg_embedding = np.mean(embeddings, axis=0)
            avg_embedding /= np.linalg.norm(avg_embedding)

            # Save
            vp_file = self.users_config.get(user_id, {}).get(
                "voiceprint_file", f"voiceprints/{user_id}.npy"
            )
            vp_path = os.path.join(self.base_dir, vp_file)
            os.makedirs(os.path.dirname(vp_path), exist_ok=True)
            np.save(vp_path, avg_embedding)

            self.voiceprints[user_id] = avg_embedding
            log.info("Enrollment completado: %s (%d muestras)", user_id, len(audio_samples))
            return True

        except Exception as e:
            log.error("Error en enrollment: %s", e)
            return False

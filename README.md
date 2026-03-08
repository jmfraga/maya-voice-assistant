# Maya - Asistente de Voz

Asistente de voz diseñada para personas mayores, ejecutándose en Raspberry Pi 5.

## Características

- **Wake word**: "Oye Maya" (Porcupine v4, español)
- **Tap-to-talk**: Botón en pantalla táctil DSI como alternativa al wake word
- **Follow-up automático**: Escucha respuesta del usuario tras cada interacción sin requerir wake word
- **STT**: OpenAI Whisper API (primario) + whisper.cpp (fallback local)
- **TTS**: OpenAI TTS voz "shimmer" (primario) / ElevenLabs (alternativo) / Piper local (fallback)
- **LLM**: Claude API (Anthropic) para generar respuestas naturales en español mexicano
- **Speaker ID**: Identificación de hablante con resemblyzer (opcional)
- **Telegram**: Bot con auto-registro y aprobación de contactos
- **Recordatorios**: Creación y notificación de recordatorios por voz
- **Medicamentos**: Registro y confirmación de tomas
- **Esquemas de tratamiento**: Dosis variable según mediciones (ej. glucosa → insulina), con alertas a familiares por Telegram si la medición sale de rango
- **Memoria inteligente**: Guardado con deduplicación y manejo de contradicciones vía LLM
- **Onboarding por voz**: Proceso guiado "Conoce a Maya" para nuevos usuarios
- **Panel admin**: Interfaz web Flask para configuración remota (medicamentos, tratamientos, contactos, memorias, API keys)
- **Display**: Interfaz Tkinter multi-pantalla fullscreen en pantalla DSI (reloj, clima, medicamentos, contactos, recordatorios)

## Módulos

| Archivo | Descripción |
|---------|-------------|
| `main.py` | Entry point y loop principal |
| `audio.py` | Grabación (pw-record), reproducción (pw-play), detección de silencio |
| `wakeword.py` | Detección de wake word con Porcupine |
| `stt.py` | Speech-to-text (OpenAI API / whisper.cpp) |
| `tts.py` | Text-to-speech (OpenAI / ElevenLabs / Piper) |
| `llm.py` | Integración con Claude/OpenAI, contexto y parsing de acciones |
| `db.py` | SQLite: usuarios, medicamentos, contactos, recordatorios, conversaciones, esquemas de tratamiento, mediciones |
| `weather.py` | Clima via OpenWeatherMap con refresh automático |
| `speaker_id.py` | Identificación de hablante por voiceprint |
| `telegram_bot.py` | Bot de Telegram con auto-registro |
| `display.py` | Interfaz gráfica Tkinter para pantalla DSI |
| `admin.py` | Panel de administración web (Flask) |

## Requisitos

### Hardware
- Raspberry Pi 5 (8GB)
- Pantalla DSI táctil
- Bocina/micrófono Bluetooth

### Software
```bash
# Dependencias del sistema
sudo apt install -y pipewire-bin libportaudio2

# Dependencias Python
pip install flask pyyaml sounddevice soundfile anthropic httpx pvporcupine resemblyzer

# Piper TTS (fallback local)
# Descargar binary y modelo es_MX-laura-high desde github.com/rhasspy/piper
```

## Configuración

1. Copiar `config.yaml.example` a `config.yaml`
2. Configurar API keys (Anthropic, OpenAI, ElevenLabs, Porcupine, Telegram)
3. Colocar archivo `.ppn` del wake word en el directorio del proyecto
4. Descargar modelo español de Porcupine (`porcupine_params_es.pv`)

El panel admin (puerto 8085) permite modificar la configuración sin editar archivos.

## Uso

```bash
cd ~/voice_assistant
python3 main.py
```

O lanzar desde el dashboard del Pi (Proyectos → Maya).

## Licencia

MIT

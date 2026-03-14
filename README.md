# Maya - Asistente de Voz

Asistente de voz diseñada para personas mayores, ejecutándose en Raspberry Pi 5 con pantalla táctil DSI y bocina/micrófono Bluetooth.

## Características

### Voz
- **Wake word**: "Oye Maya" (Porcupine v4, español)
- **Tap-to-talk**: Botón en pantalla táctil como alternativa al wake word
- **Follow-up automático**: Escucha respuesta tras cada interacción sin requerir wake word
- **Speaker ID**: Identificación de hablante con resemblyzer (opcional)

### STT / LLM / TTS (con fallback chains)
- **STT**: Synapse (M4) → OpenAI Whisper API → whisper.cpp local
- **LLM**: Synapse Smart Route (M4) → Claude (Anthropic) → OpenAI
- **TTS**: Synapse Kokoro (M4) → OpenAI TTS → ElevenLabs → Piper local
- Proveedores configurables desde panel admin con selección de fallback

### Comunicación
- **Telegram bidireccional**: Bot con auto-registro y aprobación de contactos
- **Telegram directo**: Usuarios Maya pueden chatear con Maya por Telegram (misma experiencia que voz)
- **Respuestas con nota de voz**: El bot envía texto + audio por Telegram
- **Mensajes pendientes**: Voz → Telegram y Telegram → voz
- **Comandos**: /estado, /medicamentos, /ayuda

### Salud
- **Medicamentos**: Registro, horarios, confirmación de tomas por voz
- **Recordatorios proactivos**: Maya avisa automáticamente cuando es hora de tomar un medicamento según su horario configurado (tiempos explícitos, "cada N horas", comidas, frecuencia)
- **Esquemas de tratamiento**: Dosis variable según mediciones (ej. glucosa → insulina)
- **Alertas**: Notificación a familiares por Telegram si medición fuera de rango
- **Recordatorios**: Creación y notificación por voz

### Inteligencia
- **Memoria inteligente**: Guardado con deduplicación y manejo de contradicciones vía LLM
- **Consolidación nocturna**: Cron a las 3am deduplica memorias
- **Clima**: OpenWeatherMap inyectado en contexto LLM
- **Búsqueda en internet**: Perplexity API para preguntas que Maya no sabe (noticias, datos actuales, tipo de cambio, etc.)
- **Acciones**: Tags [ACCION:...] parseados desde respuesta LLM (medicamento, recordatorio, telegram, memoria, contacto, tratamiento, buscar)
- **Entretenimiento**: Chistes, trivias, cuentos, adivinanzas — generados por el LLM, apropiados para adultos mayores

### Bienestar
- **Reportes semanales**: Resumen de salud enviado a familiares por Telegram cada domingo (medicamentos, mediciones, interacciones)
- **Monitoreo de actividad**: Maya pregunta proactivamente si el usuario lleva mucho sin interactuar (8+ horas en horario diurno)

### UX para adultos mayores
- **Onboarding 8 pasos**: Bienvenida, nombre, wake word, voiceprint, sobre ti, medicamentos, demo, despedida
- **Display**: Tkinter multi-pantalla fullscreen (reloj, clima, meds, contactos, recordatorios)
- **Layout adaptable**: 2 usuarios → botones grandes centrados; 3+ → distribución uniforme
- **Animación de escucha**: Ecualizador de 7 barras durante grabación de voz
- **Panel admin**: Interfaz web Flask para configuración remota

### Infraestructura
- **[Synapse](https://github.com/jmfraga/synapse-router)**: Mac Mini M4 Pro como servidor AI local (LLM, STT, TTS). Alternativa comercial: [OpenRouter](https://openrouter.ai/) u otros routers compatibles con OpenAI API (requiere agregar el proveedor en configuración).
- **Backup SD**: Clonación diaria a SD USB de respaldo (cron 4am, auto-detecta USB)
- **Autostart**: .desktop en ~/.config/autostart/

## Módulos

| Archivo | Descripción |
|---------|-------------|
| `main.py` | Entry point, orquesta: wake word → record → STT → LLM → acciones → TTS → follow-up |
| `audio.py` | Grabación (pw-record), reproducción (pw-play), detección de silencio |
| `wakeword.py` | Detección de wake word con Porcupine |
| `stt.py` | Speech-to-text (Synapse / OpenAI API / whisper.cpp) con initial_prompt de nombres |
| `tts.py` | Text-to-speech (Synapse / OpenAI / ElevenLabs / Piper) |
| `llm.py` | LLM con Synapse/Claude/OpenAI, contexto y parsing de acciones |
| `db.py` | SQLite: usuarios, medicamentos, contactos, recordatorios, memorias, tratamientos, mediciones |
| `weather.py` | Clima via OpenWeatherMap con refresh automático |
| `speaker_id.py` | Identificación de hablante por voiceprint |
| `telegram_bot.py` | Bot de Telegram: auto-registro, chat directo, notas de voz |
| `display.py` | Interfaz gráfica Tkinter para pantalla DSI |
| `admin.py` | Panel de administración web (Flask, puerto 8085) |
| `search.py` | Búsqueda en internet via Perplexity API |
| `consolidate_memories.py` | Cron nocturno: deduplicación de memorias via LLM |
| `scripts/backup_sd.sh` | Clonación diaria de SD a USB de respaldo |
| `scripts/telegram_notify.sh` | Notificaciones Telegram desde scripts de sistema |

## Requisitos

### Hardware
- Raspberry Pi 5 (8GB)
- Pantalla DSI táctil (480x320 o 800x480)
- Bocina/micrófono Bluetooth
- (Opcional) Mac Mini M4 Pro para Synapse AI server
- (Opcional) USB SD reader + SD de respaldo

### Software
```bash
# Dependencias del sistema
sudo apt install -y pipewire-bin libportaudio2 ffmpeg

# Dependencias Python
pip install flask pyyaml sounddevice soundfile anthropic httpx pvporcupine resemblyzer
```

## Configuración

1. Copiar `config.yaml.example` a `config.yaml`
2. Configurar API keys (Anthropic, OpenAI, Picovoice, Telegram)
3. (Opcional) Configurar Synapse base_url y api_key para servidor AI local
4. Colocar archivo `.ppn` del wake word en el directorio del proyecto

El panel admin (`http://<ip-pi>:8085`) permite modificar toda la configuración sin editar archivos.

## Uso

```bash
cd ~/voice_assistant
python3 main.py
```

Admin: `http://<ip-pi>:8085`

## Licencia

MIT

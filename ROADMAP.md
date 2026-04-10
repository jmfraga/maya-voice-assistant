# Maya Voice Assistant — Hoja de Ruta Completa

---

## Sprint 1: Core — COMPLETADO
- [x] Wake word "Oye Maya" (Porcupine v4)
- [x] Grabacion de audio (PipeWire pw-record/pw-play)
- [x] STT: OpenAI Whisper API + whisper.cpp fallback
- [x] LLM: Claude API (Anthropic)
- [x] TTS: OpenAI TTS + ElevenLabs + Piper fallback chain
- [x] Multi-turn conversation (follow-up rounds)
- [x] Persistent memory (SQLite: memorias, conversaciones)
- [x] Action tags: [ACCION:MEDICAMENTO], [ACCION:RECORDATORIO], etc.

---

## Sprint 2: Parent-friendly — COMPLETADO
- [x] Display Tkinter multi-pantalla (480x320 DSI touchscreen)
- [x] Menu de usuarios (tap-to-talk por usuario)
- [x] Onboarding basico 3-step (nombre, algo para recordar, como ayudar)
- [x] Smart memory (dedup via LLM, consolidacion nocturna)
- [x] Medication log (confirmar toma, historial)
- [x] Weather en contexto LLM — Maya responde "como esta el clima?" (2026-03-14)

---

## Sprint 2.5: Treatment Schemas — COMPLETADO
- [x] Esquemas de tratamiento variable (ej: glucosa → dosis de insulina)
- [x] Rangos configurables con alertas alto/bajo
- [x] Alertas a contactos de Telegram cuando medicion fuera de rango
- [x] Admin UI para crear/editar esquemas y rangos

---

## Sprint 3: Comunicacion — COMPLETADO
- [x] Telegram bot bidireccional (texto + voz entrante)
- [x] Self-registration flow (/start → nombre → relacion → aprobacion admin)
- [x] Pending messages (voz → Telegram, Telegram → voz)
- [x] Comandos Telegram: /estado, /medicamentos, /ayuda
- [x] Contactos por usuario en DB (nombre, chat_id, relacion, telefono)

---

## Sprint 3.5: Onboarding + Synapse + Telegram Directo — COMPLETADO (2026-03-14)

### Onboarding mejorado (8 pasos)
- [x] Step 1: Bienvenida
- [x] Step 2: Nombre preferido
- [x] Step 3: Practica wake word ("di Oye Maya")
- [x] Step 4: Voice enrollment (3 frases para voiceprint)
- [x] Step 5: Sobre ti (gustos, intereses)
- [x] Step 6: Medicamentos (extraccion via LLM, loop hasta "no mas")
- [x] Step 7: Demo interactiva (pregunta libre con respuesta real)
- [x] Step 8: Despedida + marca onboarded_at en DB
- [x] Boton cambia a "Repetir intro" despues del onboarding

### Synapse integration (Mac Mini M4 Pro)
- [x] LLM via Synapse Smart Route (maya-auto) con fallback a Claude
- [x] STT via Synapse (Whisper en M4) — funcional pero lento, dejado como fallback
- [x] TTS via Synapse (Kokoro-82M, voces: paulina/monica/jorge/juan)
- [x] Admin settings: Synapse como proveedor para LLM, STT, TTS con fallbacks
- [x] Synapse card en admin (base_url, api_key)
- [x] Bug fix: formulario de settings con dos <form> — unificado en uno

### Telegram directo para usuarios Maya
- [x] Campo telegram_chat_id en tabla users (migracion automatica)
- [x] Bot detecta si chat_id es un usuario Maya → usa llm.chat() (misma experiencia que voz)
- [x] Respuestas con nota de voz (TTS → OGG/Opus → sendVoice)
- [x] "Mandame a Telegram" desde voz: busca telegram_chat_id del usuario
- [x] _resolve_recipient busca en usuarios + contactos (match flexible)
- [x] Admin UI: campo Telegram Chat ID en cada usuario

### Otras mejoras
- [x] SSH key dedicada maya-pi desde Mac
- [x] Weather inyectado en contexto LLM
- [x] Instruccion anti-emojis en system_prompt

---

---

## Sprint 3.7: Roles y Contexto por Nivel — COMPLETADO (2026-04-10)

### Sistema de tres niveles de usuario
- [x] Columna `role` en tabla contacts (admin/contact)
- [x] Juan Manuel y Roberto marcados como admin
- [x] Deteccion automatica de rol en routing de Telegram
- [x] Metodos DB: `get_contact_with_role()`, `get_all_primary_users()`, `set_contact_role()`

### Contexto diferenciado por rol
- [x] **Admin**: meds con notas, compliance, esquemas de tratamiento, mediciones recientes, memorias completas (30), ultima interaccion, contactos
- [x] **Primario**: su info completa + resumen de otros miembros del hogar (meds tomados, ultima interaccion)
- [x] **Contacto**: meds basicos, compliance de hoy, recordatorios, memorias limitadas (10)
- [x] `build_context_with_household()` para usuarios primarios en Telegram
- [x] `chat()` acepta `include_household=True`

### System prompts por rol
- [x] Admin: tono informativo, instruccion explicita de dar info completa y detallada
- [x] Primario: tono calido (sin cambios)
- [x] Contacto: tono informativo breve

### Synapse en chat_telegram()
- [x] Soporte para provider Synapse con fallback chain (antes solo claude/openai)

### Comando /reporte (solo admins)
- [x] Reporte detallado: compliance de meds, esquemas, mediciones, recordatorios, ultima actividad
- [x] Split automatico si excede 4096 chars de Telegram
- [x] /ayuda actualizado con referencia a /reporte
- [x] Menu de comandos del bot actualizado

## Pre-Onboarding con Papas

### Limpieza del test
- [x] Quitar usuario "juanma" de config.yaml en Pi (2026-03-18)
- [x] Borrar usuario juanma de DB (delete_user desde admin) (2026-03-18)
- [x] Borrar voiceprint juanma.npy (si se creo) (2026-03-18)

### Bugs encontrados en prueba — RESUELTOS (2026-03-14)
- [x] Fecha en contexto LLM mezcla idiomas — `_fecha_es()` con lookup tables en llm.py
- [x] STT confunde nombres cortos — `set_user_names()` en stt.py, initial_prompt con nombres

### UX para adultos mayores — RESUELTOS (2026-03-14)
- [x] Layout adaptable de botones: 2 grandes centrados vs 3+ divide parejo
- [ ] Probar onboarding demo (Step 7) con clima inyectado

---

## Sprint 4: Refinamiento para Adultos Mayores — COMPLETADO (2026-03-18)

### Bug fixes
- [x] Fecha en contexto LLM en español ("Sabado 14 de Marzo" en vez de "Saturday 14 de March")
- [x] STT initial_prompt con nombres de usuarios (mejora transcripcion de nombres cortos)

### Recordatorios inteligentes
- [x] Recordatorios automaticos de medicamento si tienen horario configurado
- [x] Parser de schedules: tiempos explicitos, "cada N horas", comidas, "X veces al dia"
- [x] DB helper `is_medication_taken_today()` con ventana ±2 horas
- [x] Maya avisa proactivamente: "Juan, ya es hora de tu metformina"
- [x] Confirmar toma por voz: "ya me la tome" → registra en medication_log (ya existia via CONFIRMAR_MEDICAMENTO)

### Mejoras de display
- [x] Layout adaptable de botones: 2 usuarios → grandes centrados (350px max), 3+ → divide parejo
- [x] Animacion de escucha: 7 barras ecualizador sinusoidal sobre area de transcript
- [x] Pantalla "Mi dia": resumen matutino con scroll, filtro por dia, tono calido (2026-04-10)
- [x] Fotos de usuarios en botones (data/photos/) (2026-03-18)

### Mejoras de voz
- [ ] Speaker ID: habilitar en produccion (resemblyzer en Pi)
- [ ] Voiceprints de mama y papa (durante onboarding o manual)
- [x] Porcupine key: error handling mejorado con deteccion de key expirada (2026-03-18)

### Pre-onboarding con papas
- [x] Quitar usuario "juanma" de config.yaml en Pi (2026-03-18)
- [x] Borrar usuario juanma de DB y voiceprint (2026-03-18)
- [ ] Probar onboarding demo (Step 7) con clima inyectado

---

## Sprint 5: Internet + Entretenimiento — COMPLETADO (2026-03-14)

### Busquedas en internet
- [x] Perplexity API (search.py) — modulo nuevo, OpenAI-compatible
- [x] Accion [ACCION:BUSCAR:query] en parser + execute_actions
- [x] Resultado hablado por TTS despues de la respuesta principal
- [x] System prompt con instrucciones de cuando usar BUSCAR

### Entretenimiento
- [x] Chistes, trivias, cuentos (instrucciones en system_prompt, LLM genera directamente)
- [x] Noticias del dia via BUSCAR (el LLM usa Perplexity cuando piden noticias)
- [x] Radio/musica: 5 estaciones via ffplay/PipeWire, admin CRUD, DB-backed
- [x] Radio se pausa al hablar con Maya y reanuda al terminar
- [x] Pantalla de radio con botones de estaciones + boton apagar
- [x] Juegos de memoria / estimulacion cognitiva via system_prompt (LLM lleva el juego conversacionalmente)

### Salud y bienestar
- [x] Reportes semanales de salud via Telegram (domingos 10am, a contactos de emergencia)
- [x] Deteccion de estado de animo: audio (RMS, duracion) + texto (keywords), alerta a familia si 3/5 concernientes
- [x] Sugerencias de actividad: "llevas mucho sin hablarme, todo bien?" (8+ horas, 9am-8pm)
- [x] Contactos de emergencia: flag para separar alertas de mensajeria normal

### Admin y UX
- [x] Logo y favicon en admin
- [x] Logo en README
- [x] Admin Radio: CRUD estaciones, links a fuentes de streams
- [x] Auto-restart wrapper (scripts/run_maya.sh) + acceso directo en escritorio Pi

---

## Sprint 6: Interaccion Rapida y Barge-in — PARCIAL (2026-03-18)

### Barge-in y comandos directos
- [x] **Barge-in**: "Oye Maya" durante TTS detiene playback y reanuda escucha (2026-03-18)
- [x] **Comandos directos sin LLM**: radio play/stop, hora, fecha, clima → bypass LLM (~0.5s vs ~3-5s) (2026-04-10)
- [x] **Early intent detection**: regex matching para comandos directos antes de LLM (2026-04-10)
- [x] **"Maya cancela" / "Maya olvidalo"**: deteccion de frases de cancelacion en STT, sale de conversacion (2026-03-18)

### Saludo matutino contextualizado
- [x] Maya saluda automaticamente al primer usuario que interactua en la manana (2026-04-10)
- [x] Resumen hablado: clima, medicamentos del dia, recordatorios (2026-04-10)
- [ ] Configurable como "despertador inteligente" (hora fija o al detectar movimiento/voz)

### Analytics en admin
- [ ] **Telemetria ligera**: STT latency, LLM latency, TTS latency, errores (sin guardar audio)
- [ ] **Dashboard de uso**: interacciones por usuario, tendencias, horarios pico
- [ ] **Dashboard de salud**: cumplimiento de medicamentos, mediciones, alertas, animo
- [ ] **Privacidad**: metricas agregadas, sin contenido de conversaciones en analytics

### Personalidad configurable
- [x] UI en admin para editar personalidad de Maya con presets + texto libre (2026-03-18)
- [x] Presets: "paciente y calida", "animada y breve", "formal" (2026-03-18)

---

## Sprint 7: Robustez y Produccion — PARCIAL (2026-03-18)

### Hardening
- [ ] Verificar fallback chains completas (LLM/STT/TTS) con pruebas de fallo
- [ ] Manejo de errores de red (WiFi intermitente en casa de papas, retry con backoff)
- [x] systemd user service (scripts/maya.service) con auto-restart (2026-03-18)
- [ ] UPS/no-break para RPi

### Monitoreo
- [x] Health check con alerta Telegram si Maya se cae (scripts/health_check.py, cron 5min) (2026-03-18)
- [ ] Metricas exportadas (Prometheus-compatible o SQLite simple)

### Mejoras de audio
- [ ] VAD avanzado (Silero) para ambientes ruidosos (TV, cocina)
- [ ] Streaming STT / transcripcion parcial para reducir latencia
- [ ] Confidence scoring en STT — si baja, pedir que repita

### Multi-usuario avanzado
- [ ] Guest mode: visitantes pueden hablar sin registro
- [ ] MEMORIA_TERCERO: guardar info que un usuario dice sobre otro
- [ ] Perfiles de voz ajustados por usuario (velocidad TTS, volumen)

---

## Infraestructura

- [x] Backup SD automatico: script + cron diario 4am en Pi (2026-03-14)
- [ ] Ollama en M4: modelos locales como alternativa a APIs
- [x] M4 como servidor AI centralizado (Synapse: Maya + MedExpert)
- [ ] Monitoring (Prometheus + Grafana)
- [ ] UPS para mini rack (M1 + M4 + RPi + networking)
- [ ] Pantalla mas grande para Pi (800x480 → 7" o 10")

---

## Sprint 8: Proactividad Inteligente

### Tono de fin de respuesta
- [ ] Sonido suave al terminar de hablar (reduce incertidumbre de "ya termino?")

### Watchdog de audio
- [ ] Verificar rutinariamente que mic y altavoz BT estan activos
- [ ] Si no responden, reconectar automaticamente (bluetoothctl + pw-play test)
- [ ] Log/alerta si falla tras N reintentos

### Check-ins proactivos por Telegram
- [ ] Maya envia mensaje a usuarios primarios si llevan varias horas sin interaccion
- [ ] Recordatorio por Telegram si medicamentos clave no confirmados pasada cierta hora
- [ ] Check-in general a hora configurable ("Como va tu dia? Necesitas algo?")
- [ ] Seguimiento post-medicion fuera de rango
- [ ] Tono calido y natural, no alertas clinicas frias
- [ ] Prerequisito: todos los primarios deben tener telegram_chat_id (papa pendiente)
- [ ] Configurable: activar/desactivar por usuario desde admin

### Aprendizaje de patrones de rutina
- [ ] Registrar timestamps de acciones del usuario (radio, medicamentos, clima, etc.) en DB
- [ ] Heuristicas simples: clustering por ventana de tiempo, frecuencia ultimos N dias
- [ ] Cuando un patron se detecta consistentemente, Maya sugiere proactivamente:
  - "Quieres que ponga la radio?" (si siempre pone radio ~9am)
  - "Te recuerdo tu medicina?" (si siempre pregunta ~8:30)
  - "Llamamos a alguien?" (si siempre llama los domingos)
- [ ] Sugerencias siempre opcionales, nunca acciones automaticas
- [ ] Almacenamiento ligero en SQLite (sin ML pesado)
- [ ] Configurable: activar/desactivar sugerencias por usuario desde admin

---

## Sprint 9: Spotify por Voz

### Control de Spotify via Web API
- [ ] OAuth flow: autorizacion de cuenta Spotify (Premium requerido)
- [ ] Comandos por voz: "Maya, pon mi playlist de rancheras", "Maya, siguiente cancion"
- [ ] Acciones: [ACCION:SPOTIFY_PLAY:query], [ACCION:SPOTIFY_PAUSE], [ACCION:SPOTIFY_NEXT], [ACCION:SPOTIFY_PREV]
- [ ] Busqueda de canciones, artistas, playlists, albums
- [ ] Transfer playback al dispositivo "Maya" (Spotify Connect)
- [ ] "Que cancion es esta?" → metadata del track actual
- [ ] Integrar con radio: "pon radio" vs "pon Spotify" como fuentes de audio separadas
- [ ] Pausar Spotify al hablar con Maya (como radio)

### Prerequisito
- [x] Spotify Connect basico via raspotify (instalado 2026-03-15)

---

## Ideas a Futuro

- [ ] Camara: reconocimiento facial como complemento a voiceprint
- [ ] Domotica: control de luces, TV, aire acondicionado via Maya
- [ ] Video llamadas: "Maya, llamale a Juanma" → Telegram video call
- [ ] Calendario familiar compartido
- [ ] Compras: "Maya, agregame leche a la lista" → lista compartida
- [ ] Emergencias: deteccion de caidas (acelerometro), boton de panico
- [ ] Multi-idioma: soporte para ingles (para cuando tengan visitas)

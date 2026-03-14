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

## Pre-Onboarding con Papas

### Limpieza del test
- [ ] Quitar usuario "juanma" de config.yaml en Pi
- [ ] Borrar usuario juanma de DB (delete_user desde admin)
- [ ] Borrar voiceprint juanma.npy (si se creo)

### Bugs encontrados en prueba — RESUELTOS (2026-03-14)
- [x] Fecha en contexto LLM mezcla idiomas — `_fecha_es()` con lookup tables en llm.py
- [x] STT confunde nombres cortos — `set_user_names()` en stt.py, initial_prompt con nombres

### UX para adultos mayores — RESUELTOS (2026-03-14)
- [x] Layout adaptable de botones: 2 grandes centrados vs 3+ divide parejo
- [ ] Probar onboarding demo (Step 7) con clima inyectado

---

## Sprint 4: Refinamiento para Adultos Mayores — EN PROGRESO (2026-03-14)

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
- [ ] Pantalla "Mi dia": resumen matutino (clima, meds pendientes, recordatorios)
- [ ] Fotos de usuarios en botones (data/photos/)

### Mejoras de voz
- [ ] Speaker ID: habilitar en produccion (resemblyzer en Pi)
- [ ] Voiceprints de mama y papa (durante onboarding o manual)
- [ ] Porcupine key renewal (clave actual puede expirar)

### Pre-onboarding con papas
- [ ] Quitar usuario "juanma" de config.yaml en Pi
- [ ] Borrar usuario juanma de DB y voiceprint
- [ ] Probar onboarding demo (Step 7) con clima inyectado

---

## Sprint 5: Internet + Entretenimiento — EN PROGRESO (2026-03-14)

### Busquedas en internet
- [x] Perplexity API (search.py) — modulo nuevo, OpenAI-compatible
- [x] Accion [ACCION:BUSCAR:query] en parser + execute_actions
- [x] Resultado hablado por TTS despues de la respuesta principal
- [x] System prompt con instrucciones de cuando usar BUSCAR

### Entretenimiento
- [x] Chistes, trivias, cuentos (instrucciones en system_prompt, LLM genera directamente)
- [x] Noticias del dia via BUSCAR (el LLM usa Perplexity cuando piden noticias)
- [ ] Radio/musica (streams de radio mexicana via PipeWire)
- [ ] Juegos de memoria / estimulacion cognitiva (interactivos)

### Salud y bienestar
- [x] Reportes semanales de salud via Telegram (domingos 10am, a todos los contactos)
- [ ] Deteccion de estado de animo por voz (tono, velocidad)
- [x] Sugerencias de actividad: "llevas mucho sin hablarme, todo bien?" (8+ horas, 9am-8pm)

---

## Sprint 6: Robustez y Escala

### Hardening
- [ ] Fallback LLM: Synapse → Claude → OpenAI (ya implementado, verificar)
- [ ] Fallback TTS: Synapse → OpenAI → ElevenLabs → Piper (ya implementado, verificar)
- [ ] Fallback STT: OpenAI API → Synapse → whisper.cpp (ya implementado, verificar)
- [ ] Manejo de errores de red (WiFi intermitente en casa de papas)
- [ ] Autostart robusto: systemd service en lugar de .desktop autostart
- [ ] UPS/no-break para RPi

### Monitoreo
- [ ] Health check: Telegram alert si Maya se cae
- [ ] Metricas: tiempo de respuesta, errores STT, uso de API
- [ ] Dashboard en admin: estadisticas de uso por usuario

### Multi-usuario avanzado
- [ ] Guest mode: visitantes pueden hablar sin registro
- [ ] MEMORIA_TERCERO: guardar info que un usuario dice sobre otro
- [ ] Perfiles de voz ajustados por usuario (velocidad TTS, volumen)

---

## Infraestructura

- [ ] Backup SD automatico: USB SD reader + cron diario clona SD principal (script listo, falta instalar cron en Pi)
- [ ] Ollama en M4: modelos locales como alternativa a APIs
- [x] M4 como servidor AI centralizado (Synapse: Maya + MedExpert)
- [ ] Monitoring (Prometheus + Grafana)
- [ ] UPS para mini rack (M1 + M4 + RPi + networking)
- [ ] Pantalla mas grande para Pi (800x480 → 7" o 10")

---

## Ideas a Futuro

- [ ] Camara: reconocimiento facial como complemento a voiceprint
- [ ] Domotica: control de luces, TV, aire acondicionado via Maya
- [ ] Video llamadas: "Maya, llamale a Juanma" → Telegram video call
- [ ] Calendario familiar compartido
- [ ] Compras: "Maya, agregame leche a la lista" → lista compartida
- [ ] Emergencias: deteccion de caidas (acelerometro), boton de panico
- [ ] Multi-idioma: soporte para ingles (para cuando tengan visitas)

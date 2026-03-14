#!/bin/bash
# backup_sd.sh — Clone main SD card to USB backup SD
# Runs daily via cron. If no USB SD detected, exits silently.
#
# Usage: sudo bash backup_sd.sh
# Cron:  0 4 * * * /home/jmfraga/voice_assistant/scripts/backup_sd.sh >> /home/jmfraga/voice_assistant/logs/backup.log 2>&1

set -euo pipefail

LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
SOURCE_DEV="/dev/mmcblk0"       # Main SD card
BACKUP_DEV=""                    # Auto-detected USB SD
TELEGRAM_SCRIPT="/home/jmfraga/voice_assistant/scripts/telegram_notify.sh"

log() { echo "$LOG_PREFIX $1"; }

# --- Detect USB SD card ---
# Look for /dev/sda (USB SD reader) — skip if not present
for dev in /dev/sda /dev/sdb; do
    if [ -b "$dev" ] && [ "$dev" != "$SOURCE_DEV" ]; then
        # Verify it's a removable USB device
        dev_name=$(basename "$dev")
        removable=$(cat "/sys/block/$dev_name/removable" 2>/dev/null || echo "0")
        if [ "$removable" = "1" ] || grep -q "usb" "/sys/block/$dev_name/device/uevent" 2>/dev/null; then
            BACKUP_DEV="$dev"
            break
        fi
    fi
done

if [ -z "$BACKUP_DEV" ]; then
    # No USB SD detected — exit silently (not an error)
    exit 0
fi

log "Backup SD detectada: $BACKUP_DEV"

# --- Safety checks ---
# Don't clone onto the boot device
ROOT_DEV=$(findmnt -n -o SOURCE / | sed 's/p[0-9]*$//')
if [ "$BACKUP_DEV" = "$ROOT_DEV" ]; then
    log "ERROR: $BACKUP_DEV es el dispositivo de arranque, abortando"
    exit 1
fi

# Check source exists
if [ ! -b "$SOURCE_DEV" ]; then
    log "ERROR: Dispositivo fuente $SOURCE_DEV no encontrado"
    exit 1
fi

# Get sizes
SOURCE_SIZE=$(blockdev --getsize64 "$SOURCE_DEV" 2>/dev/null || echo 0)
BACKUP_SIZE=$(blockdev --getsize64 "$BACKUP_DEV" 2>/dev/null || echo 0)

if [ "$BACKUP_SIZE" -lt "$SOURCE_SIZE" ]; then
    log "ERROR: Backup SD ($((BACKUP_SIZE/1024/1024))MB) es menor que source ($((SOURCE_SIZE/1024/1024))MB)"
    exit 1
fi

log "Iniciando clonacion: $SOURCE_DEV ($((SOURCE_SIZE/1024/1024))MB) -> $BACKUP_DEV"

# --- Unmount backup partitions ---
for part in "${BACKUP_DEV}"*; do
    if mountpoint -q "$part" 2>/dev/null || mount | grep -q "$part"; then
        log "Desmontando $part"
        umount "$part" 2>/dev/null || true
    fi
done

# --- Clone ---
START=$(date +%s)

dd if="$SOURCE_DEV" of="$BACKUP_DEV" bs=4M status=none conv=fsync

END=$(date +%s)
DURATION=$(( END - START ))
MINUTES=$(( DURATION / 60 ))

log "Clonacion completada en ${MINUTES}m${DURATION}s"

# --- Notify via Telegram (optional) ---
if [ -x "$TELEGRAM_SCRIPT" ]; then
    "$TELEGRAM_SCRIPT" "Backup SD completado en ${MINUTES} minutos" 2>/dev/null || true
fi

log "Backup exitoso"

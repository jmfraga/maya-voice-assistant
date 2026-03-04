"""Display module: Tkinter fullscreen UI on DSI touchscreen (800x480)."""

import tkinter as tk
import threading
import queue
import logging
from datetime import datetime

log = logging.getLogger("maya.display")


class Display:
    """Fullscreen display for Maya assistant on DSI touchscreen."""

    def __init__(self, on_close=None, on_talk=None):
        self.queue = queue.Queue()
        self.on_close = on_close      # callback when user taps Salir
        self.on_talk = on_talk        # callback when user taps Hablar (tap-to-talk)
        self.root = None
        self._thread = None
        self._running = False
        self._talk_btn = None

    def start(self):
        """Start display in a separate thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self.root = tk.Tk()
        self.root.title("Maya")
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="#1a1a2e")
        self.root.overrideredirect(True)

        # Colors
        BG = "#1a1a2e"
        CARD_BG = "#16213e"
        TEXT = "#e8e8e8"
        ACCENT = "#e94560"
        MUTED = "#8899aa"
        SUCCESS = "#4ecca3"

        W, H = 800, 480

        canvas = tk.Canvas(self.root, width=W, height=H, bg=BG, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # --- Clock (top right) ---
        self.clock_label = tk.Label(
            canvas, text="", font=("Helvetica", 36, "bold"),
            fg=TEXT, bg=BG, anchor="e",
        )
        self.clock_label.place(x=W - 20, y=15, anchor="ne")

        self.date_label = tk.Label(
            canvas, text="", font=("Helvetica", 14),
            fg=MUTED, bg=BG, anchor="e",
        )
        self.date_label.place(x=W - 20, y=60, anchor="ne")

        # --- Maya title + status (top left) ---
        tk.Label(
            canvas, text="Maya", font=("Helvetica", 28, "bold"),
            fg=ACCENT, bg=BG,
        ).place(x=20, y=15)

        self.status_label = tk.Label(
            canvas, text="Iniciando...", font=("Helvetica", 16),
            fg=SUCCESS, bg=BG,
        )
        self.status_label.place(x=20, y=55)

        # --- User label ---
        self.user_label = tk.Label(
            canvas, text="", font=("Helvetica", 14),
            fg=MUTED, bg=BG,
        )
        self.user_label.place(x=20, y=85)

        # --- Divider ---
        canvas.create_line(20, 115, W - 20, 115, fill="#2a2a4a", width=2)

        # --- Transcription area ---
        tk.Label(
            canvas, text="Usted dijo:", font=("Helvetica", 11),
            fg=MUTED, bg=BG, anchor="w",
        ).place(x=20, y=125)

        self.transcript_label = tk.Label(
            canvas, text="...", font=("Helvetica", 16),
            fg=TEXT, bg=CARD_BG, anchor="nw", justify="left",
            wraplength=W - 60, padx=10, pady=8,
        )
        self.transcript_label.place(x=20, y=150, width=W - 40, height=60)

        # --- Response area ---
        tk.Label(
            canvas, text="Maya:", font=("Helvetica", 11),
            fg=MUTED, bg=BG, anchor="w",
        ).place(x=20, y=220)

        self.response_label = tk.Label(
            canvas, text="...", font=("Helvetica", 16),
            fg=TEXT, bg=CARD_BG, anchor="nw", justify="left",
            wraplength=W - 60, padx=10, pady=8,
        )
        self.response_label.place(x=20, y=245, width=W - 40, height=80)

        # --- Divider ---
        canvas.create_line(20, 340, W - 20, 340, fill="#2a2a4a", width=2)

        # --- Reminders section ---
        tk.Label(
            canvas, text="Proximos recordatorios", font=("Helvetica", 12, "bold"),
            fg=ACCENT, bg=BG, anchor="w",
        ).place(x=20, y=350)

        self.reminders_label = tk.Label(
            canvas, text="Sin recordatorios pendientes", font=("Helvetica", 13),
            fg=MUTED, bg=BG, anchor="nw", justify="left",
            wraplength=360,
        )
        self.reminders_label.place(x=20, y=378, width=380, height=90)

        # --- Bottom right: Talk button + Exit button ---
        # Talk button (tap-to-talk, large and friendly)
        self._talk_btn = tk.Button(
            canvas, text="Toca para\nhablar", font=("Helvetica", 16, "bold"),
            bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92",
            width=12, height=3, relief="flat", bd=0,
            command=self._on_talk_pressed,
        )
        self._talk_btn.place(x=440, y=355, width=200, height=100)

        # Exit button (smaller, top-right area)
        tk.Button(
            canvas, text="Salir", font=("Helvetica", 12, "bold"),
            bg=ACCENT, fg=TEXT, activebackground="#d03050",
            width=8, relief="flat", bd=0,
            command=self._on_close_pressed,
        ).place(x=670, y=370, width=110, height=40)

        # Start clock update
        self._update_clock()

        # Process queue
        self._process_queue()

        self.root.mainloop()

    def _on_talk_pressed(self):
        """Handle tap-to-talk button press."""
        if self.on_talk:
            # Run callback in a thread to not block Tkinter
            threading.Thread(target=self.on_talk, daemon=True).start()

    def _on_close_pressed(self):
        """Handle exit button press."""
        if self.on_close:
            self.on_close()
        self.stop()

    def _update_clock(self):
        if not self._running:
            return
        now = datetime.now()
        self.clock_label.config(text=now.strftime("%H:%M"))
        self.date_label.config(text=now.strftime("%A %d de %B"))
        if self.root:
            self.root.after(1000, self._update_clock)

    def _process_queue(self):
        if not self._running:
            return
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        if self.root:
            self.root.after(100, self._process_queue)

    def _handle_message(self, msg: dict):
        kind = msg.get("type", "")
        if kind == "status":
            self.status_label.config(text=msg["text"])
            color = msg.get("color", "#4ecca3")
            self.status_label.config(fg=color)
        elif kind == "user":
            self.user_label.config(text=msg.get("name", ""))
        elif kind == "transcript":
            self.transcript_label.config(text=msg["text"])
        elif kind == "response":
            self.response_label.config(text=msg["text"])
        elif kind == "reminders":
            self.reminders_label.config(text=msg["text"])
        elif kind == "talk_btn":
            # Enable/disable talk button
            if self._talk_btn:
                if msg.get("enabled", True):
                    self._talk_btn.config(
                        state="normal", bg="#4ecca3", text="Toca para\nhablar",
                    )
                else:
                    self._talk_btn.config(
                        state="disabled", bg="#2a4a3a", text="Procesando...",
                    )

    # --- Public API (thread-safe) ---

    def set_status(self, text: str, color: str = "#4ecca3"):
        self.queue.put({"type": "status", "text": text, "color": color})

    def set_user(self, name: str):
        self.queue.put({"type": "user", "name": name})

    def set_transcript(self, text: str):
        self.queue.put({"type": "transcript", "text": text})

    def set_response(self, text: str):
        self.queue.put({"type": "response", "text": text})

    def set_reminders(self, text: str):
        self.queue.put({"type": "reminders", "text": text})

    def enable_talk_btn(self, enabled: bool = True):
        self.queue.put({"type": "talk_btn", "enabled": enabled})

    def stop(self):
        self._running = False
        if self.root:
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass

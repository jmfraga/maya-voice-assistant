"""Display module: Multi-screen Tkinter UI on DSI touchscreen (800x480).

Screens:
  - Main: clock, weather, alerts, user buttons
  - UserMenu: action buttons per user
  - Medications: list with taken/pending status
  - Conversation: transcript + response during active chat
  - Night: dim clock only (10pm-7am)
"""

import tkinter as tk
import threading
import queue
import logging
import os
from datetime import datetime

log = logging.getLogger("maya.display")

# --- Theme (light, high contrast) ---
BG = "#F0F0F0"
CARD_BG = "#FFFFFF"
TEXT = "#1A1A1A"
TEXT_SEC = "#555555"
ACCENT = "#E94560"
SUCCESS = "#27AE60"
WARNING = "#E67E22"
DANGER = "#C0392B"
MUTED = "#999999"
NIGHT_BG = "#111111"
NIGHT_TEXT = "#AAAAAA"

USER_COLORS = ["#2980B9", "#27AE60", "#8E44AD", "#D35400"]

REF_W, REF_H = 800, 480  # Reference resolution
W, H = 800, 480  # Actual resolution (updated at runtime)
AUTO_RETURN_MS = 120_000  # 2 minutes
NIGHT_START = 22  # 10pm
NIGHT_END = 7     # 7am

# Spanish day/month names
DAYS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
MONTHS = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
          "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _greeting():
    h = datetime.now().hour
    if h < 12:
        return "Buenos dias"
    elif h < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _weather_icon(icon_code: str) -> str:
    mapping = {
        "01d": "\u2600", "01n": "\u263E", "02d": "\u26C5", "02n": "\u26C5",
        "03d": "\u2601", "03n": "\u2601", "04d": "\u2601", "04n": "\u2601",
        "09d": "\u2614", "09n": "\u2614", "10d": "\u2614", "10n": "\u2614",
        "11d": "\u26A1", "11n": "\u26A1", "13d": "\u2744", "13n": "\u2744",
        "50d": "\u2588", "50n": "\u2588",
    }
    return mapping.get(icon_code, "")


class Display:
    """Multi-screen fullscreen display for Maya on DSI touchscreen."""

    def __init__(self, config: dict, db=None, weather=None,
                 on_close=None, on_talk=None, on_user_talk=None):
        self.queue = queue.Queue()
        self.config = config
        self.db = db
        self.weather = weather
        self.on_close = on_close
        self.on_talk = on_talk
        self.on_user_talk = on_user_talk  # callback(user_id)
        self.root = None
        self._thread = None
        self._running = False
        self._auto_return_after_id = None
        self._night_mode = False
        self._current_screen = "main"
        self.active_user_id = None  # set when user taps their button to talk
        self._photo_images = {}  # keep references to prevent GC

        # Users: prefer DB names (editable via admin), fallback to config
        self._users = []
        db_users = {u["id"]: u["real_name"] for u in db.get_users()} if db else {}
        for i, (uid, ucfg) in enumerate(config.get("users", {}).items()):
            name = db_users.get(uid, ucfg.get("real_name", uid))
            self._users.append({
                "id": uid,
                "name": name,
                "color": USER_COLORS[i % len(USER_COLORS)],
            })

    def x(self, val):
        """Scale X coordinate."""
        return int(val * self._sx) if hasattr(self, '_sx') else val

    def y(self, val):
        """Scale Y coordinate."""
        return int(val * self._sy) if hasattr(self, '_sy') else val

    def fs(self, val):
        """Scale font size."""
        return max(8, int(val * self._sf)) if hasattr(self, '_sf') else val

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        global W, H
        self.root = tk.Tk()
        self.root.title("Maya")
        self.root.configure(bg=BG)
        # Detect actual screen resolution
        W = self.root.winfo_screenwidth()
        H = self.root.winfo_screenheight()
        log.info("Pantalla detectada: %dx%d", W, H)
        # Scale factor for fonts and sizes
        self._sx = W / REF_W
        self._sy = H / REF_H
        self._sf = min(self._sx, self._sy)  # uniform scale factor
        self.root.geometry(f"{W}x{H}+0+0")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Container for all screens
        self._container = tk.Frame(self.root, bg=BG)
        self._container.place(x=0, y=0, width=W, height=H)

        # Build screens
        self._screens = {}
        self._build_main_screen()
        self._build_conversation_screen()
        self._build_night_screen()

        # Show main
        self._show_screen("main")

        # Start timers
        self._update_clock()
        self._process_queue()
        self._check_night_mode()

        self.root.mainloop()

    # ===== SCREEN BUILDERS =====

    def _build_main_screen(self):
        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens["main"] = f

        # --- Top section: clock left, info right ---
        top = tk.Frame(f, bg=CARD_BG, height=self.y(130))
        top.place(x=0, y=0, width=W, height=self.y(130))

        # Clock (left side, big)
        self._clock_label = tk.Label(
            top, text="", font=("Helvetica", self.fs(72), "bold"),
            fg=TEXT, bg=CARD_BG, anchor="w",
        )
        self._clock_label.place(x=self.x(20), y=self.y(10), height=self.y(90))

        # Right side: greeting, date, weather stacked
        self._greeting_label = tk.Label(
            top, text=_greeting(), font=("Helvetica", self.fs(20), "bold"),
            fg=TEXT, bg=CARD_BG, anchor="e",
        )
        self._greeting_label.place(x=W - self.x(20), y=self.y(10), anchor="ne")

        self._date_label = tk.Label(
            top, text="", font=("Helvetica", self.fs(17)),
            fg=TEXT_SEC, bg=CARD_BG, anchor="e",
        )
        self._date_label.place(x=W - self.x(20), y=self.y(45), anchor="ne")

        self._weather_label = tk.Label(
            top, text="", font=("Helvetica", self.fs(18)),
            fg=TEXT_SEC, bg=CARD_BG, anchor="e",
        )
        self._weather_label.place(x=W - self.x(20), y=self.y(80), anchor="ne")

        # --- Alert bar ---
        alert_frame = tk.Frame(f, bg=BG, height=self.y(50))
        alert_frame.place(x=0, y=self.y(135), width=W, height=self.y(50))

        self._alert_left = tk.Label(
            alert_frame, text="", font=("Helvetica", self.fs(15)),
            fg=WARNING, bg=BG, anchor="w", cursor="hand2",
        )
        self._alert_left.place(x=self.x(25), y=self.y(5), width=self.x(370), height=self.y(40))
        self._alert_left.bind("<Button-1>", self._on_alert_tap)

        self._alert_right = tk.Label(
            alert_frame, text="", font=("Helvetica", self.fs(15)),
            fg=ACCENT, bg=BG, anchor="w",
        )
        self._alert_right.place(x=self.x(410), y=self.y(5), width=self.x(370), height=self.y(40))

        # --- Separator ---
        tk.Frame(f, bg="#DDDDDD", height=self.y(2)).place(x=self.x(20), y=self.y(188), width=W - self.x(40), height=self.y(2))

        # --- User buttons ---
        btn_frame = tk.Frame(f, bg=BG)
        btn_frame.place(x=0, y=self.y(193), width=W, height=self.y(235))

        num_users = len(self._users)
        if num_users == 0:
            tk.Label(btn_frame, text="Sin usuarios configurados",
                     font=("Helvetica", self.fs(16)), fg=MUTED, bg=BG).place(
                relx=0.5, rely=0.5, anchor="center")
        else:
            btn_w = (W - self.x(40) - (num_users - 1) * self.x(15)) // num_users
            for i, user in enumerate(self._users):
                bx = self.x(20) + i * (btn_w + self.x(15))
                self._create_user_button(btn_frame, user, bx, self.y(5), btn_w, self.y(225))

        # --- Bottom status bar ---
        bottom = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        bottom.place(x=0, y=H - self.y(48), width=W, height=self.y(48))

        self._status_dot = tk.Label(
            bottom, text="\u25CF", font=("Helvetica", self.fs(14)),
            fg=SUCCESS, bg=CARD_BG,
        )
        self._status_dot.place(x=self.x(15), y=self.y(8), height=self.y(32))

        self._status_label = tk.Label(
            bottom, text="Maya: Lista", font=("Helvetica", self.fs(15)),
            fg=TEXT_SEC, bg=CARD_BG, anchor="w",
        )
        self._status_label.place(x=self.x(38), y=self.y(8), height=self.y(32))

        self._mic_hint = tk.Label(
            bottom, text="Di 'Oye Maya' o toca tu nombre",
            font=("Helvetica", self.fs(13)), fg=MUTED, bg=CARD_BG, anchor="e",
        )
        self._mic_hint.place(x=self.x(380), y=self.y(8), width=self.x(280), height=self.y(32))

        # Admin URL (local network)
        try:
            import subprocess as _sp
            _lip = _sp.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
            _local_ip = _lip.stdout.strip().split()[0] if _lip.stdout.strip() else None
        except Exception:
            _local_ip = None
        if _local_ip:
            self._admin_url_label = tk.Label(
                f, text=f"Admin: http://{_local_ip}:8085",
                font=("Helvetica", self.fs(11)), fg=MUTED, bg=BG, anchor="w",
            )
            self._admin_url_label.place(x=self.x(25), y=self.y(185), width=W - self.x(30), height=self.y(15))

        # Config + Exit buttons (hidden in production mode)
        if not self.config.get("production_mode", False):
            config_btn = tk.Label(
                bottom, text="\u2699", font=("Helvetica", self.fs(18)),
                fg=TEXT_SEC, bg=CARD_BG, cursor="hand2",
            )
            config_btn.place(x=W - self.x(100), y=self.y(5), width=self.x(40), height=self.y(38))
            config_btn.bind("<Button-1>", lambda e: self._show_config_screen())

            exit_btn = tk.Label(
                bottom, text="\u2716", font=("Helvetica", self.fs(16)),
                fg=DANGER, bg=CARD_BG, cursor="hand2",
            )
            exit_btn.place(x=W - self.x(50), y=self.y(7), width=self.x(40), height=self.y(34))
            exit_btn.bind("<Button-1>", lambda e: self._on_close_pressed())

    def _create_user_button(self, parent, user, x, y, w, h):
        """Create a large touchable user button with photo or initial."""
        color = user["color"]
        uid = user["id"]
        name = user["name"]

        btn = tk.Frame(parent, bg=color, cursor="hand2",
                       highlightthickness=0, bd=0)
        btn.place(x=x, y=y, width=w, height=h)

        # Try to load photo
        photo_label = None
        base = os.path.join(os.path.dirname(__file__), "data", "photos")
        for ext in (".jpg", ".jpeg", ".png"):
            path = os.path.join(base, f"{uid}{ext}")
            if os.path.isfile(path):
                try:
                    from PIL import Image, ImageTk, ImageDraw
                    img = Image.open(path)
                    size = min(w - self.x(20), self.y(90))
                    img = img.resize((size, size), Image.LANCZOS)
                    # Circular mask
                    mask = Image.new("L", (size, size), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
                    img.putalpha(mask)
                    tk_img = ImageTk.PhotoImage(img)
                    self._photo_images[uid] = tk_img
                    photo_label = tk.Label(btn, image=tk_img, bg=color, bd=0)
                    photo_label.place(relx=0.5, y=self.y(10), anchor="n")
                except Exception as e:
                    log.warning("Error cargando foto %s: %s", uid, e)
                break

        if not photo_label:
            # Show initial in circle
            initial = name[0].upper() if name else "?"
            c_size = self.fs(80)
            circle = tk.Canvas(btn, width=c_size, height=c_size, bg=color,
                               highlightthickness=0)
            circle.place(relx=0.5, y=self.y(15), anchor="n")
            # Lighter circle background
            circle.create_oval(c_size * 5 // 80, c_size * 5 // 80,
                               c_size * 75 // 80, c_size * 75 // 80,
                               fill="white", outline="")
            circle.create_text(c_size // 2, c_size // 2, text=initial,
                               font=("Helvetica", self.fs(36), "bold"), fill=color)

        # Name label
        name_lbl = tk.Label(
            btn, text=name, font=("Helvetica", self.fs(22), "bold"),
            fg="white", bg=color,
        )
        name_lbl.place(relx=0.5, y=h - self.y(35), anchor="center")

        # Bind click to all children
        for widget in [btn, name_lbl]:
            widget.bind("<Button-1>", lambda e, u=uid: self._open_user_menu(u))
        if photo_label:
            photo_label.bind("<Button-1>", lambda e, u=uid: self._open_user_menu(u))

    def _build_conversation_screen(self):
        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens["conversation"] = f

        # Header
        header = tk.Frame(f, bg=ACCENT, height=self.y(50))
        header.place(x=0, y=0, width=W, height=self.y(50))

        back_btn = tk.Label(
            header, text="\u2190 Inicio", font=("Helvetica", self.fs(16), "bold"),
            fg="white", bg=ACCENT, cursor="hand2",
        )
        back_btn.place(x=self.x(10), y=self.y(8), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e: self._conv_go_home())

        self._conv_title = tk.Label(
            header, text="Maya", font=("Helvetica", self.fs(20), "bold"),
            fg="white", bg=ACCENT,
        )
        self._conv_title.place(x=self.x(130), y=self.y(8))

        self._conv_status = tk.Label(
            header, text="Escuchando...", font=("Helvetica", self.fs(16)),
            fg="#FFD0D0", bg=ACCENT, anchor="e",
        )
        self._conv_status.place(x=W - self.x(220), y=self.y(12), width=self.x(200))

        # User said
        tk.Label(f, text="Dijiste:", font=("Helvetica", self.fs(13)),
                 fg=TEXT_SEC, bg=BG).place(x=self.x(20), y=self.y(65))

        self._conv_transcript = tk.Label(
            f, text="...", font=("Helvetica", self.fs(18)),
            fg=TEXT, bg=CARD_BG, anchor="nw", justify="left",
            wraplength=W - self.x(60), padx=self.x(15), pady=self.y(10),
        )
        self._conv_transcript.place(x=self.x(20), y=self.y(95), width=W - self.x(40), height=self.y(100))

        # Maya response
        tk.Label(f, text="Maya:", font=("Helvetica", self.fs(13)),
                 fg=TEXT_SEC, bg=BG).place(x=self.x(20), y=self.y(210))

        self._conv_response = tk.Label(
            f, text="...", font=("Helvetica", self.fs(18)),
            fg=TEXT, bg=CARD_BG, anchor="nw", justify="left",
            wraplength=W - self.x(60), padx=self.x(15), pady=self.y(10),
        )
        self._conv_response.place(x=self.x(20), y=self.y(240), width=W - self.x(40), height=self.y(120))

        # Reminders
        tk.Label(f, text="Recordatorios:", font=("Helvetica", self.fs(13), "bold"),
                 fg=ACCENT, bg=BG).place(x=self.x(20), y=self.y(375))

        self._conv_reminders = tk.Label(
            f, text="", font=("Helvetica", self.fs(14)),
            fg=TEXT_SEC, bg=BG, anchor="nw", justify="left",
            wraplength=self.x(380),
        )
        self._conv_reminders.place(x=self.x(20), y=self.y(400), width=self.x(400), height=self.y(70))

        # Talk button
        self._conv_talk_btn = tk.Button(
            f, text="Toca para\nhablar", font=("Helvetica", self.fs(18), "bold"),
            bg=SUCCESS, fg="white", activebackground="#219A52",
            relief="flat", bd=0, command=self._on_talk_pressed,
        )
        self._conv_talk_btn.place(x=W - self.x(220), y=self.y(380), width=self.x(200), height=self.y(90))

    def _build_night_screen(self):
        f = tk.Frame(self._container, bg=NIGHT_BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens["night"] = f

        self._night_clock = tk.Label(
            f, text="", font=("Helvetica", self.fs(100), "bold"),
            fg=NIGHT_TEXT, bg=NIGHT_BG,
        )
        self._night_clock.place(relx=0.5, rely=0.45, anchor="center")

        self._night_date = tk.Label(
            f, text="", font=("Helvetica", self.fs(20)),
            fg="#555555", bg=NIGHT_BG,
        )
        self._night_date.place(relx=0.5, rely=0.65, anchor="center")

        # Tap anywhere to wake
        f.bind("<Button-1>", lambda e: self._exit_night_mode())

    def _build_user_menu(self, user_id: str):
        """Build or rebuild a user menu screen."""
        screen_name = f"user_{user_id}"

        # Destroy old if exists
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user:
            return
        color = user["color"]
        name = user["name"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        # Header with back button
        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))

        back_btn = tk.Label(
            header, text="\u2190 Inicio", font=("Helvetica", self.fs(16), "bold"),
            fg="white", bg=color, cursor="hand2",
        )
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e: self._go_home())

        tk.Label(
            header, text=name, font=("Helvetica", self.fs(22), "bold"),
            fg="white", bg=color,
        ).place(relx=0.5, y=self.y(10), anchor="n")

        # Action buttons grid (3 columns x 3 rows)
        actions = [
            ("Hablar con\nMaya", "\U0001F3A4", SUCCESS,
             lambda u=user_id: self._user_talk(u)),
            ("Mi dia", "\U0001F4CB", "#3498DB",
             lambda u=user_id: self._show_my_day(u)),
            ("Medicamentos", "\U0001F48A", "#E67E22",
             lambda u=user_id: self._show_medications(u)),
            ("Contactos", "\U0001F465", "#9B59B6",
             lambda u=user_id: self._show_contacts(u)),
            ("Recordatorios", "\u23F0", "#1ABC9C",
             lambda u=user_id: self._show_reminders(u)),
            ("Tratamiento", "\U0001F489", "#16A085",
             lambda u=user_id: self._show_treatments(u)),
            ("Repetir\nintro" if self.db and self.db.is_onboarded(user_id)
             else "Conoce a\nMaya", "\u2B50", ACCENT,
             lambda u=user_id: self._start_onboarding(u)),
        ]

        btn_w = self.x(235)
        btn_h = self.y(105)
        start_x = self.x(30)
        start_y = self.y(65)
        gap_x = self.x(25)
        gap_y = self.y(12)

        for i, (label, icon, btn_color, cmd) in enumerate(actions):
            col = i % 3
            row = i // 3
            bx = start_x + col * (btn_w + gap_x)
            by = start_y + row * (btn_h + gap_y)

            btn = tk.Frame(f, bg=btn_color, cursor="hand2")
            btn.place(x=bx, y=by, width=btn_w, height=btn_h)

            icon_lbl = tk.Label(
                btn, text=icon, font=("Helvetica", self.fs(28)),
                fg="white", bg=btn_color,
            )
            icon_lbl.place(relx=0.5, y=self.y(15), anchor="n")

            text_lbl = tk.Label(
                btn, text=label, font=("Helvetica", self.fs(15), "bold"),
                fg="white", bg=btn_color, justify="center",
            )
            text_lbl.place(relx=0.5, y=self.y(60), anchor="n")

            for w in [btn, icon_lbl, text_lbl]:
                w.bind("<Button-1>", lambda e, c=cmd: c())

    def _build_medications_screen(self, user_id: str):
        """Build medications list screen for a user."""
        screen_name = f"meds_{user_id}"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user or not self.db:
            return
        color = user["color"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        # Header
        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))

        back_btn = tk.Label(
            header, text="\u2190 Menu", font=("Helvetica", self.fs(16), "bold"),
            fg="white", bg=color, cursor="hand2",
        )
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>",
                      lambda e, u=user_id: self._open_user_menu(u))

        tk.Label(
            header, text=f"Medicamentos - {user['name']}",
            font=("Helvetica", self.fs(20), "bold"), fg="white", bg=color,
        ).place(relx=0.5, y=self.y(10), anchor="n")

        # Scrollable area via canvas
        canvas = tk.Canvas(f, bg=BG, highlightthickness=0)
        canvas.place(x=0, y=self.y(60), width=W, height=H - self.y(110))

        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw", width=W)

        # Get medications and today's log
        meds = self.db.get_medications(user_id, active_only=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_today = self.db.get_medication_log(user_id, date=today)
        taken_ids = {entry["medication_id"] for entry in log_today}

        if not meds:
            tk.Label(inner, text="No hay medicamentos registrados",
                     font=("Helvetica", self.fs(18)), fg=MUTED, bg=BG,
                     ).pack(pady=self.y(40))
        else:
            for med in meds:
                taken = med["id"] in taken_ids
                self._create_med_row(inner, med, taken, user_id, color)

        # Bottom: home button
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(
            home_bar, text="\u2302 Inicio", font=("Helvetica", self.fs(16), "bold"),
            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2",
        )
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

    def _create_med_row(self, parent, med, taken, user_id, color):
        row = tk.Frame(parent, bg=CARD_BG, height=self.y(70))
        row.pack(fill="x", padx=self.x(15), pady=self.y(5))
        row.pack_propagate(False)

        # Status indicator
        status_color = SUCCESS if taken else WARNING
        status_text = "\u2714 Tomado" if taken else "\u23F3 Pendiente"

        tk.Label(
            row, text="\u25CF", font=("Helvetica", self.fs(20)),
            fg=status_color, bg=CARD_BG,
        ).place(x=self.x(10), y=self.y(15))

        tk.Label(
            row, text=med["name"], font=("Helvetica", self.fs(18), "bold"),
            fg=TEXT, bg=CARD_BG, anchor="w",
        ).place(x=self.x(40), y=self.y(8), width=self.x(300))

        info = ""
        if med.get("dosage"):
            info += med["dosage"]
        if med.get("schedule"):
            info += f"  |  {med['schedule']}"
        tk.Label(
            row, text=info, font=("Helvetica", self.fs(13)),
            fg=TEXT_SEC, bg=CARD_BG, anchor="w",
        ).place(x=self.x(40), y=self.y(38), width=self.x(400))

        # Status / confirm button
        if taken:
            tk.Label(
                row, text=status_text, font=("Helvetica", self.fs(14), "bold"),
                fg=SUCCESS, bg=CARD_BG,
            ).place(x=W - self.x(180), y=self.y(18), width=self.x(150), anchor="nw")
        else:
            confirm_btn = tk.Label(
                row, text="Marcar tomado", font=("Helvetica", self.fs(14), "bold"),
                fg="white", bg=SUCCESS, cursor="hand2",
                padx=self.x(10), pady=self.y(5),
            )
            confirm_btn.place(x=W - self.x(200), y=self.y(12), width=self.x(160), height=self.y(45))
            confirm_btn.bind("<Button-1>",
                             lambda e, m=med, u=user_id:
                             self._confirm_med(m["id"], u))

    def _build_contacts_screen(self, user_id: str):
        screen_name = f"contacts_{user_id}"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user or not self.db:
            return
        color = user["color"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        # Header
        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))
        back_btn = tk.Label(header, text="\u2190 Menu",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg="white", bg=color, cursor="hand2")
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e, u=user_id: self._open_user_menu(u))

        tk.Label(header, text=f"Contactos - {user['name']}",
                 font=("Helvetica", self.fs(20), "bold"), fg="white", bg=color,
                 ).place(relx=0.5, y=self.y(10), anchor="n")

        contacts = self.db.get_contacts(user_id)
        cy = self.y(70)
        if not contacts:
            tk.Label(f, text="No hay contactos registrados",
                     font=("Helvetica", self.fs(18)), fg=MUTED, bg=BG).place(x=self.x(20), y=cy)
        else:
            for c in contacts[:6]:
                row = tk.Frame(f, bg=CARD_BG, height=self.y(60))
                row.place(x=self.x(15), y=cy, width=W - self.x(30), height=self.y(55))
                tk.Label(row, text=c["name"], font=("Helvetica", self.fs(17), "bold"),
                         fg=TEXT, bg=CARD_BG).place(x=self.x(15), y=self.y(5))
                info = c.get("relationship", "")
                if c.get("phone"):
                    info += f"  |  {c['phone']}"
                tk.Label(row, text=info, font=("Helvetica", self.fs(13)),
                         fg=TEXT_SEC, bg=CARD_BG).place(x=self.x(15), y=self.y(30))
                cy += self.y(62)

        # Bottom home
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(home_bar, text="\u2302 Inicio",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2")
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

    def _build_reminders_screen(self, user_id: str):
        screen_name = f"reminders_{user_id}"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user or not self.db:
            return
        color = user["color"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))
        back_btn = tk.Label(header, text="\u2190 Menu",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg="white", bg=color, cursor="hand2")
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e, u=user_id: self._open_user_menu(u))

        tk.Label(header, text=f"Recordatorios - {user['name']}",
                 font=("Helvetica", self.fs(20), "bold"), fg="white", bg=color,
                 ).place(relx=0.5, y=self.y(10), anchor="n")

        reminders = self.db.get_pending_reminders(user_id)
        ry = self.y(70)
        if not reminders:
            tk.Label(f, text="No hay recordatorios pendientes",
                     font=("Helvetica", self.fs(18)), fg=MUTED, bg=BG).place(x=self.x(20), y=ry)
        else:
            for r in reminders[:7]:
                row = tk.Frame(f, bg=CARD_BG, height=self.y(50))
                row.place(x=self.x(15), y=ry, width=W - self.x(30), height=self.y(48))
                tk.Label(row, text=f"\u23F0 {r['remind_at']}",
                         font=("Helvetica", self.fs(16), "bold"),
                         fg=ACCENT, bg=CARD_BG).place(x=self.x(15), y=self.y(10))
                tk.Label(row, text=r["text"], font=("Helvetica", self.fs(15)),
                         fg=TEXT, bg=CARD_BG).place(x=self.x(120), y=self.y(10))
                ry += self.y(55)

        # Bottom home
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(home_bar, text="\u2302 Inicio",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2")
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

    def _build_treatments_screen(self, user_id: str):
        """Build treatment schemas screen for a user (read-only view)."""
        screen_name = f"treatments_{user_id}"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user or not self.db:
            return
        color = user["color"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        # Header
        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))

        back_btn = tk.Label(
            header, text="\u2190 Menu", font=("Helvetica", self.fs(16), "bold"),
            fg="white", bg=color, cursor="hand2",
        )
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>",
                      lambda e, u=user_id: self._open_user_menu(u))

        tk.Label(
            header, text=f"Tratamiento - {user['name']}",
            font=("Helvetica", self.fs(20), "bold"), fg="white", bg=color,
        ).place(relx=0.5, y=self.y(10), anchor="n")

        # Scrollable area
        canvas = tk.Canvas(f, bg=BG, highlightthickness=0)
        canvas.place(x=0, y=self.y(60), width=W, height=H - self.y(110))

        inner = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=inner, anchor="nw", width=W)

        schemas = self.db.get_treatment_schemas(user_id, active_only=True)

        if not schemas:
            tk.Label(inner, text="No hay esquemas de tratamiento",
                     font=("Helvetica", self.fs(18)), fg=MUTED, bg=BG,
                     ).pack(pady=self.y(40))
        else:
            for schema in schemas:
                self._create_treatment_card(inner, schema, color)

        # Bottom home
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(
            home_bar, text="\u2302 Inicio", font=("Helvetica", self.fs(16), "bold"),
            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2",
        )
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

    def _create_treatment_card(self, parent, schema, color):
        """Create a card showing a treatment schema with its dose ranges."""
        ranges = self.db.get_treatment_ranges(schema["id"])
        unit = schema.get("measurement_unit", "")
        m_name = schema.get("measurement_name", "")

        # Card frame
        card = tk.Frame(parent, bg=CARD_BG)
        card.pack(fill="x", padx=self.x(15), pady=self.y(8))

        # Schema name header
        name_lbl = tk.Label(
            card, text=f"\U0001F489 {schema['name']}",
            font=("Helvetica", self.fs(18), "bold"), fg=TEXT, bg=CARD_BG, anchor="w",
        )
        name_lbl.pack(fill="x", padx=self.x(15), pady=(self.y(10), self.y(2)))

        # What is measured
        tk.Label(
            card, text=f"Medicion: {m_name} ({unit})" if unit else f"Medicion: {m_name}",
            font=("Helvetica", self.fs(14)), fg=TEXT_SEC, bg=CARD_BG, anchor="w",
        ).pack(fill="x", padx=self.x(15), pady=(0, self.y(5)))

        # Alert thresholds
        alert_parts = []
        if schema.get("alert_low") is not None:
            alert_parts.append(f"Alerta si < {schema['alert_low']}{unit}")
        if schema.get("alert_high") is not None:
            alert_parts.append(f"Alerta si > {schema['alert_high']}{unit}")
        if alert_parts:
            tk.Label(
                card, text="  |  ".join(alert_parts),
                font=("Helvetica", self.fs(13)), fg=DANGER, bg=CARD_BG, anchor="w",
            ).pack(fill="x", padx=self.x(15), pady=(0, self.y(5)))

        # Dose ranges
        if ranges:
            for r in ranges:
                range_text = (
                    f"  {r['range_min']}-{r['range_max']}{unit}  \u2192  "
                    f"{r['dose']} {r.get('dose_unit', '')}"
                )
                if r.get("time_of_day") and r["time_of_day"] != "any":
                    range_text += f"  ({r['time_of_day']})"
                tk.Label(
                    card, text=range_text, font=("Helvetica", self.fs(15)),
                    fg=TEXT, bg=CARD_BG, anchor="w",
                ).pack(fill="x", padx=self.x(15), pady=self.y(1))
        else:
            tk.Label(
                card, text="  Sin rangos configurados",
                font=("Helvetica", self.fs(14)), fg=MUTED, bg=CARD_BG, anchor="w",
            ).pack(fill="x", padx=self.x(15), pady=self.y(2))

        # Notes
        if schema.get("notes"):
            tk.Label(
                card, text=schema["notes"],
                font=("Helvetica", self.fs(12)), fg=MUTED, bg=CARD_BG, anchor="w",
            ).pack(fill="x", padx=self.x(15), pady=(self.y(5), self.y(10)))
        else:
            # Bottom padding
            tk.Frame(card, bg=CARD_BG, height=self.y(10)).pack()

    def _build_my_day_screen(self, user_id: str):
        screen_name = f"myday_{user_id}"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        user = next((u for u in self._users if u["id"] == user_id), None)
        if not user or not self.db:
            return
        color = user["color"]

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        header = tk.Frame(f, bg=color, height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))
        back_btn = tk.Label(header, text="\u2190 Menu",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg="white", bg=color, cursor="hand2")
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e, u=user_id: self._open_user_menu(u))

        tk.Label(header, text=f"Mi dia - {user['name']}",
                 font=("Helvetica", self.fs(20), "bold"), fg="white", bg=color,
                 ).place(relx=0.5, y=self.y(10), anchor="n")

        dy = self.y(70)

        # Weather
        if self.weather:
            wd = self.weather.data
            if wd:
                icon = _weather_icon(wd.get("icon", ""))
                tk.Label(f, text=f"{icon} {wd['temp']}\u00B0C - {wd['description']}",
                         font=("Helvetica", self.fs(20)), fg=TEXT, bg=BG).place(x=self.x(20), y=dy)
                dy += self.y(40)

        # Medications status
        tk.Label(f, text="Medicamentos:", font=("Helvetica", self.fs(17), "bold"),
                 fg=WARNING, bg=BG).place(x=self.x(20), y=dy)
        dy += self.y(30)

        meds = self.db.get_medications(user_id, active_only=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_today = self.db.get_medication_log(user_id, date=today)
        taken_ids = {e["medication_id"] for e in log_today}

        if not meds:
            tk.Label(f, text="  Sin medicamentos", font=("Helvetica", self.fs(15)),
                     fg=MUTED, bg=BG).place(x=self.x(20), y=dy)
            dy += self.y(25)
        else:
            for med in meds:
                taken = med["id"] in taken_ids
                icon = "\u2714" if taken else "\u23F3"
                clr = SUCCESS if taken else WARNING
                tk.Label(f, text=f"  {icon} {med['name']}",
                         font=("Helvetica", self.fs(15)), fg=clr, bg=BG).place(x=self.x(20), y=dy)
                dy += self.y(25)

        dy += self.y(15)

        # Reminders
        tk.Label(f, text="Recordatorios:", font=("Helvetica", self.fs(17), "bold"),
                 fg=ACCENT, bg=BG).place(x=self.x(20), y=dy)
        dy += self.y(30)

        reminders = self.db.get_pending_reminders(user_id)
        if not reminders:
            tk.Label(f, text="  Sin recordatorios", font=("Helvetica", self.fs(15)),
                     fg=MUTED, bg=BG).place(x=self.x(20), y=dy)
        else:
            for r in reminders[:5]:
                tk.Label(f, text=f"  \u23F0 {r['remind_at']} - {r['text']}",
                         font=("Helvetica", self.fs(15)), fg=TEXT, bg=BG).place(x=self.x(20), y=dy)
                dy += self.y(25)

        # Bottom home
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(home_bar, text="\u2302 Inicio",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2")
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

    def _build_config_screen(self):
        screen_name = "config"
        if screen_name in self._screens:
            self._screens[screen_name].destroy()

        f = tk.Frame(self._container, bg=BG)
        f.place(x=0, y=0, width=W, height=H)
        self._screens[screen_name] = f

        header = tk.Frame(f, bg="#34495E", height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))
        back_btn = tk.Label(header, text="\u2190 Inicio",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg="white", bg="#34495E", cursor="hand2")
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e: self._go_home())

        tk.Label(header, text="Configuracion",
                 font=("Helvetica", self.fs(20), "bold"), fg="white", bg="#34495E",
                 ).place(relx=0.5, y=self.y(10), anchor="n")

        cy = self.y(75)

        # --- Bluetooth section ---
        tk.Label(f, text="Bluetooth - Bocina/Microfono",
                 font=("Helvetica", self.fs(18), "bold"), fg=TEXT, bg=BG,
                 ).place(x=self.x(20), y=cy)
        cy += self.y(35)

        self._bt_status_label = tk.Label(
            f, text="Verificando...", font=("Helvetica", self.fs(15)),
            fg=TEXT_SEC, bg=BG, anchor="w",
        )
        self._bt_status_label.place(x=self.x(20), y=cy, width=self.x(500))

        bt_reconnect = tk.Label(
            f, text="Reconectar", font=("Helvetica", self.fs(15), "bold"),
            fg="white", bg="#3498DB", cursor="hand2", padx=self.x(15), pady=self.y(5),
        )
        bt_reconnect.place(x=W - self.x(170), y=cy - self.y(5), width=self.x(150), height=self.y(40))
        bt_reconnect.bind("<Button-1>", lambda e: self._bt_reconnect())
        cy += self.y(50)

        # --- WiFi section ---
        tk.Frame(f, bg="#DDDDDD", height=self.y(2)).place(x=self.x(20), y=cy, width=W - self.x(40))
        cy += self.y(15)

        tk.Label(f, text="WiFi",
                 font=("Helvetica", self.fs(18), "bold"), fg=TEXT, bg=BG,
                 ).place(x=self.x(20), y=cy)
        cy += self.y(35)

        self._wifi_status_label = tk.Label(
            f, text="Verificando...", font=("Helvetica", self.fs(15)),
            fg=TEXT_SEC, bg=BG, anchor="w",
        )
        self._wifi_status_label.place(x=self.x(20), y=cy, width=self.x(600))
        cy += self.y(35)

        wifi_connect_btn = tk.Label(
            f, text="Conectar WiFi", font=("Helvetica", self.fs(15), "bold"),
            fg="white", bg="#3498DB", cursor="hand2", padx=self.x(15), pady=self.y(5),
        )
        wifi_connect_btn.place(x=W - self.x(170), y=cy - self.y(5), width=self.x(150), height=self.y(40))
        wifi_connect_btn.bind("<Button-1>", lambda e: self._wifi_connect_dialog())

        self._wifi_ip_label = tk.Label(
            f, text="", font=("Helvetica", self.fs(14)),
            fg=MUTED, bg=BG, anchor="w",
        )
        self._wifi_ip_label.place(x=self.x(20), y=cy + self.y(30), width=self.x(600))
        cy += self.y(60)

        # --- System info ---
        tk.Frame(f, bg="#DDDDDD", height=self.y(2)).place(x=self.x(20), y=cy, width=W - self.x(40))
        cy += self.y(15)

        tk.Label(f, text="Sistema",
                 font=("Helvetica", self.fs(18), "bold"), fg=TEXT, bg=BG,
                 ).place(x=self.x(20), y=cy)
        cy += self.y(35)

        self._sys_info_label = tk.Label(
            f, text="", font=("Helvetica", self.fs(14)),
            fg=TEXT_SEC, bg=BG, anchor="nw", justify="left",
        )
        self._sys_info_label.place(x=self.x(20), y=cy, width=self.x(600), height=self.y(60))

        # Bottom home
        home_bar = tk.Frame(f, bg=CARD_BG, height=self.y(48))
        home_bar.place(x=0, y=H - self.y(48), width=W, height=self.y(48))
        home_btn = tk.Label(home_bar, text="\u2302 Inicio",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg=TEXT_SEC, bg=CARD_BG, cursor="hand2")
        home_btn.place(relx=0.5, rely=0.5, anchor="center")
        home_btn.bind("<Button-1>", lambda e: self._go_home())

        # Fetch status in background
        threading.Thread(target=self._fetch_config_status, daemon=True).start()

    def _fetch_config_status(self):
        """Fetch BT, WiFi, system info in background thread."""
        import subprocess

        # Bluetooth
        try:
            bt_mac = self.config.get("audio", {}).get("bt_device_mac", "")
            if bt_mac:
                result = subprocess.run(
                    ["bluetoothctl", "info", bt_mac],
                    capture_output=True, text=True, timeout=5,
                )
                if "Connected: yes" in result.stdout:
                    name = ""
                    for line in result.stdout.splitlines():
                        if "Name:" in line:
                            name = line.split("Name:")[1].strip()
                    bt_text = f"Conectado: {name} ({bt_mac})"
                    bt_color = SUCCESS
                else:
                    bt_text = f"Desconectado: {bt_mac}"
                    bt_color = WARNING
            else:
                bt_text = "No configurado"
                bt_color = MUTED
        except Exception:
            bt_text = "Error verificando Bluetooth"
            bt_color = DANGER

        self.queue.put({"type": "_config_bt", "text": bt_text, "color": bt_color})

        # WiFi
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                capture_output=True, text=True, timeout=5,
            )
            wifi_text = "No conectado"
            for line in result.stdout.strip().splitlines():
                parts = line.split(":")
                if parts[0] == "yes":
                    wifi_text = f"Conectado: {parts[1]} (senal: {parts[2]}%)"
                    break
        except Exception:
            wifi_text = "Error verificando WiFi"

        # IP
        try:
            result = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=3,
            )
            ip_text = f"IP: {result.stdout.strip()}"
        except Exception:
            ip_text = ""

        self.queue.put({"type": "_config_wifi", "text": wifi_text, "ip": ip_text})

        # System
        try:
            with open("/proc/uptime") as uf:
                uptime_s = float(uf.read().split()[0])
                hours = int(uptime_s // 3600)
                mins = int((uptime_s % 3600) // 60)
                uptime = f"Encendido: {hours}h {mins}m"

            with open("/sys/class/thermal/thermal_zone0/temp") as tf:
                temp = int(tf.read().strip()) / 1000
                temp_text = f"Temperatura: {temp:.1f} C"

            sys_text = f"{uptime}  |  {temp_text}"
        except Exception:
            sys_text = ""

        self.queue.put({"type": "_config_sys", "text": sys_text})

    def _bt_reconnect(self):
        """Try to reconnect Bluetooth speaker."""
        import subprocess
        bt_mac = self.config.get("audio", {}).get("bt_device_mac", "")
        if not bt_mac:
            return
        if hasattr(self, "_bt_status_label"):
            self.queue.put({"type": "_config_bt",
                           "text": "Reconectando...", "color": WARNING})

        def _do_reconnect():
            try:
                subprocess.run(
                    ["bluetoothctl", "connect", bt_mac],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
            # Re-fetch status
            self._fetch_config_status()

        threading.Thread(target=_do_reconnect, daemon=True).start()

    def _wifi_connect_dialog(self):
        """Open WiFi scan & connect overlay."""
        import subprocess as sp

        overlay = tk.Frame(self._container, bg=BG)
        overlay.place(x=0, y=0, width=W, height=H)
        overlay.lift()

        header = tk.Frame(overlay, bg="#34495E", height=self.y(55))
        header.place(x=0, y=0, width=W, height=self.y(55))
        tk.Label(header, text="Conectar WiFi",
                 font=("Helvetica", self.fs(20), "bold"), fg="white", bg="#34495E",
                 ).place(relx=0.5, y=self.y(10), anchor="n")

        def _close_overlay():
            overlay.destroy()
            # Refresh config status
            threading.Thread(target=self._fetch_config_status,
                           daemon=True).start()

        back_btn = tk.Label(header, text="\u2190 Volver",
                            font=("Helvetica", self.fs(16), "bold"),
                            fg="white", bg="#34495E", cursor="hand2")
        back_btn.place(x=self.x(15), y=self.y(10), height=self.y(35))
        back_btn.bind("<Button-1>", lambda e: _close_overlay())

        # Network list
        listframe = tk.Frame(overlay, bg=BG)
        listframe.place(x=self.x(20), y=self.y(70), width=W - self.x(40), height=self.y(280))

        scrollbar = tk.Scrollbar(listframe)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(listframe, font=("Helvetica", self.fs(16)),
                             bg=CARD_BG, fg=TEXT, selectbackground=ACCENT,
                             yscrollcommand=scrollbar.set, height=8)
        listbox.pack(fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        status_lbl = tk.Label(overlay, text="Escaneando...",
                              font=("Helvetica", self.fs(14)), fg=TEXT_SEC, bg=BG)
        status_lbl.place(x=self.x(20), y=self.y(360), width=W - self.x(40), height=self.y(30))

        # Buttons
        btn_frame = tk.Frame(overlay, bg=BG)
        btn_frame.place(x=self.x(20), y=self.y(400), width=W - self.x(40), height=self.y(50))

        connect_btn = tk.Label(btn_frame, text="Conectar",
                               font=("Helvetica", self.fs(16), "bold"),
                               fg="white", bg=SUCCESS, cursor="hand2",
                               padx=self.x(20), pady=self.y(8))
        connect_btn.pack(side="left", padx=self.x(10))

        rescan_btn = tk.Label(btn_frame, text="Rescan",
                              font=("Helvetica", self.fs(16), "bold"),
                              fg="white", bg=WARNING, cursor="hand2",
                              padx=self.x(20), pady=self.y(8))
        rescan_btn.pack(side="left", padx=self.x(10))

        close_btn = tk.Label(btn_frame, text="Cerrar",
                             font=("Helvetica", self.fs(16), "bold"),
                             fg="white", bg=DANGER, cursor="hand2",
                             padx=self.x(20), pady=self.y(8))
        close_btn.pack(side="right", padx=self.x(10))
        close_btn.bind("<Button-1>", lambda e: _close_overlay())

        def _scan():
            status_lbl.config(text="Escaneando...")
            listbox.delete(0, tk.END)

            def _do_scan():
                try:
                    sp.run(["sudo", "-n", "nmcli", "device", "wifi", "rescan"],
                           timeout=10, capture_output=True)
                    import time
                    time.sleep(2)
                    r = sp.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
                                "device", "wifi", "list", "--rescan", "no"],
                               capture_output=True, text=True, timeout=10)
                    networks = []
                    seen = set()
                    for line in r.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        parts = line.split(":")
                        ssid = parts[0] if parts else ""
                        signal = parts[1] if len(parts) > 1 else "?"
                        security = parts[2] if len(parts) > 2 else ""
                        if ssid and ssid not in seen:
                            seen.add(ssid)
                            lock = " [protegida]" if security else ""
                            networks.append(f"{ssid}  ({signal}%){lock}")
                    self.queue.put({"type": "_wifi_scan_done",
                                   "networks": networks})
                except Exception as ex:
                    self.queue.put({"type": "_wifi_scan_done",
                                   "error": str(ex)})

            threading.Thread(target=_do_scan, daemon=True).start()

        def _connect():
            sel = listbox.curselection()
            if not sel:
                status_lbl.config(text="Selecciona una red primero")
                return
            entry = listbox.get(sel[0])
            ssid = entry.split("  (")[0]

            if "[protegida]" in entry:
                # Show password entry inline
                _show_password_entry(ssid)
            else:
                _do_connect(["nmcli", "device", "wifi", "connect", ssid], ssid)

        def _show_password_entry(ssid):
            kbd_frame = tk.Frame(overlay, bg=BG)
            kbd_frame.place(x=0, y=0, width=W, height=H)
            kbd_frame.lift()

            shift_on = [False]
            show_pwd = [False]

            # --- Top bar: label + entry + show/hide ---
            top = tk.Frame(kbd_frame, bg=CARD_BG)
            top.place(x=0, y=0, width=W, height=self.y(70))

            tk.Label(top, text=f"{ssid}:",
                     font=("Helvetica", self.fs(13)), fg=TEXT, bg=CARD_BG,
                     ).place(x=self.x(5), y=self.y(5))

            pwd_entry = tk.Entry(top, font=("Helvetica", self.fs(16)),
                                 show="*", width=20)
            pwd_entry.place(x=self.x(5), y=self.y(32), width=W - self.x(80), height=self.y(32))

            def _toggle_show():
                show_pwd[0] = not show_pwd[0]
                pwd_entry.config(show="" if show_pwd[0] else "*")
                eye_btn.config(text="***" if show_pwd[0] else "Aa")

            eye_btn = tk.Label(top, text="Aa",
                               font=("Helvetica", self.fs(12), "bold"),
                               fg="white", bg="#7F8C8D", cursor="hand2")
            eye_btn.place(x=W - self.x(70), y=self.y(32), width=self.x(60), height=self.y(32))
            eye_btn.bind("<Button-1>", lambda e: _toggle_show())

            # --- Keyboard rows ---
            rows_lower = [
                list("1234567890"),
                list("qwertyuiop"),
                list("asdfghjkl"),
                list("zxcvbnm"),
            ]
            rows_upper = [
                list("!@#$%^&*()"),
                list("QWERTYUIOP"),
                list("ASDFGHJKL"),
                list("ZXCVBNM"),
            ]

            key_labels = []
            kb_area = tk.Frame(kbd_frame, bg=BG)
            kb_area.place(x=0, y=self.y(72), width=W, height=H - self.y(72))

            row_height = (H - self.y(72)) // 5
            key_font = ("Helvetica", self.fs(13), "bold")
            key_bg = "#ECF0F1"

            def _press(ch):
                pwd_entry.insert(tk.END, ch)

            def _backspace():
                pwd_entry.delete(len(pwd_entry.get()) - 1, tk.END)

            def _toggle_shift():
                shift_on[0] = not shift_on[0]
                rows = rows_upper if shift_on[0] else rows_lower
                idx = 0
                for r, row in enumerate(rows):
                    for c, ch in enumerate(row):
                        if idx < len(key_labels):
                            key_labels[idx].config(text=ch)
                        idx += 1
                shift_btn.config(bg=ACCENT if shift_on[0] else key_bg,
                                 fg="white" if shift_on[0] else TEXT)

            def _submit_pwd():
                pwd = pwd_entry.get()
                kbd_frame.destroy()
                if pwd:
                    _do_connect(["nmcli", "device", "wifi", "connect",
                                 ssid, "password", pwd], ssid)

            def _cancel_pwd():
                kbd_frame.destroy()

            for r, row in enumerate(rows_lower):
                num_keys = len(row)
                key_w = (W - self.x(10)) // 10
                x_offset = (W - num_keys * key_w) // 2
                for c, ch in enumerate(row):
                    btn = tk.Label(kb_area, text=ch, font=key_font,
                                   fg=TEXT, bg=key_bg, relief="raised",
                                   cursor="hand2")
                    btn.place(x=x_offset + c * key_w, y=r * row_height,
                              width=key_w - 2, height=row_height - 2)
                    btn.bind("<Button-1>",
                             lambda e, b=btn: _press(b.cget("text")))
                    key_labels.append(btn)

            # Bottom row: shift, space, backspace, OK, cancel
            bot_y = 4 * row_height
            btn_w = W // 6

            shift_btn = tk.Label(kb_area, text="^", font=key_font,
                                 fg=TEXT, bg=key_bg, relief="raised", cursor="hand2")
            shift_btn.place(x=2, y=bot_y, width=btn_w - 2, height=row_height - 2)
            shift_btn.bind("<Button-1>", lambda e: _toggle_shift())

            space_btn = tk.Label(kb_area, text="___", font=key_font,
                                 fg=TEXT, bg=key_bg, relief="raised", cursor="hand2")
            space_btn.place(x=btn_w, y=bot_y, width=btn_w * 2 - 2, height=row_height - 2)
            space_btn.bind("<Button-1>", lambda e: _press(" "))

            bksp_btn = tk.Label(kb_area, text="<", font=key_font,
                                fg=TEXT, bg=key_bg, relief="raised", cursor="hand2")
            bksp_btn.place(x=btn_w * 3, y=bot_y, width=btn_w - 2, height=row_height - 2)
            bksp_btn.bind("<Button-1>", lambda e: _backspace())

            ok_btn = tk.Label(kb_area, text="OK", font=key_font,
                              fg="white", bg=SUCCESS, relief="raised", cursor="hand2")
            ok_btn.place(x=btn_w * 4, y=bot_y, width=btn_w - 2, height=row_height - 2)
            ok_btn.bind("<Button-1>", lambda e: _submit_pwd())

            cancel_btn = tk.Label(kb_area, text="X", font=key_font,
                                  fg="white", bg=DANGER, relief="raised", cursor="hand2")
            cancel_btn.place(x=btn_w * 5, y=bot_y, width=btn_w - 2, height=row_height - 2)
            cancel_btn.bind("<Button-1>", lambda e: _cancel_pwd())

        def _do_connect(cmd, ssid):
            status_lbl.config(text=f"Conectando a {ssid}...")

            def _run():
                try:
                    r = sp.run(cmd, capture_output=True, text=True, timeout=30)
                    if r.returncode == 0:
                        self.queue.put({"type": "_wifi_connect_result",
                                       "text": f"Conectado a {ssid}!"})
                    else:
                        self.queue.put({"type": "_wifi_connect_result",
                                       "text": f"Error: {r.stderr.strip()}"})
                except Exception as ex:
                    self.queue.put({"type": "_wifi_connect_result",
                                   "text": f"Error: {ex}"})

            threading.Thread(target=_run, daemon=True).start()

        # Store refs for queue handler
        self._wifi_dialog_listbox = listbox
        self._wifi_dialog_status = status_lbl

        connect_btn.bind("<Button-1>", lambda e: _connect())
        rescan_btn.bind("<Button-1>", lambda e: _scan())

        _scan()

    def _show_config_screen(self):
        self._build_config_screen()
        self._show_screen("config")

    # ===== NAVIGATION =====

    def _show_screen(self, name):
        if name in self._screens:
            self._screens[name].lift()
            self._current_screen = name
            self._reset_auto_return()

    def _go_home(self):
        self._cancel_auto_return()
        self._refresh_main_screen()
        self._show_screen("main")

    def _open_user_menu(self, user_id):
        self._build_user_menu(user_id)
        self._show_screen(f"user_{user_id}")

    def _show_medications(self, user_id):
        self._build_medications_screen(user_id)
        self._show_screen(f"meds_{user_id}")

    def _show_contacts(self, user_id):
        self._build_contacts_screen(user_id)
        self._show_screen(f"contacts_{user_id}")

    def _show_reminders(self, user_id):
        self._build_reminders_screen(user_id)
        self._show_screen(f"reminders_{user_id}")

    def _show_my_day(self, user_id):
        self._build_my_day_screen(user_id)
        self._show_screen(f"myday_{user_id}")

    def _show_treatments(self, user_id):
        self._build_treatments_screen(user_id)
        self._show_screen(f"treatments_{user_id}")

    def _start_onboarding(self, user_id):
        # Trigger onboarding via voice — will be expanded in Phase 3
        self.active_user_id = user_id
        if self.on_user_talk:
            threading.Thread(
                target=self.on_user_talk,
                args=(user_id, "onboarding"),
                daemon=True,
            ).start()

    def _user_talk(self, user_id):
        self.active_user_id = user_id
        if self.on_user_talk:
            threading.Thread(
                target=self.on_user_talk,
                args=(user_id, "talk"),
                daemon=True,
            ).start()

    def _confirm_med(self, med_id, user_id):
        if self.db:
            self.db.confirm_medication(med_id, user_id)
            # Rebuild medications screen
            self._show_medications(user_id)

    def _conv_go_home(self):
        """Return home from conversation screen, clearing user context."""
        self.active_user_id = None
        self._go_home()

    def _on_alert_tap(self, event):
        """Tap on medication alert -> open first user's medications."""
        if self._users:
            self._show_medications(self._users[0]["id"])

    # ===== AUTO-RETURN =====

    def _reset_auto_return(self):
        self._cancel_auto_return()
        if self._current_screen not in ("main", "night", "conversation"):
            self._auto_return_after_id = self.root.after(
                AUTO_RETURN_MS, self._go_home)

    def _cancel_auto_return(self):
        if self._auto_return_after_id and self.root:
            self.root.after_cancel(self._auto_return_after_id)
            self._auto_return_after_id = None

    # ===== NIGHT MODE =====

    def _check_night_mode(self):
        if not self._running:
            return
        hour = datetime.now().hour
        should_night = hour >= NIGHT_START or hour < NIGHT_END

        if should_night and not self._night_mode and self._current_screen == "main":
            self._enter_night_mode()
        elif not should_night and self._night_mode:
            self._exit_night_mode()

        if self.root:
            self.root.after(60_000, self._check_night_mode)  # check every minute

    def _enter_night_mode(self):
        self._night_mode = True
        self._show_screen("night")

    def _exit_night_mode(self):
        self._night_mode = False
        self._go_home()

    # ===== TIMER CALLBACKS =====

    def _update_clock(self):
        if not self._running:
            return
        now = datetime.now()
        time_str = now.strftime("%H:%M")
        day_name = DAYS[now.weekday()]
        date_str = f"{day_name} {now.day} de {MONTHS[now.month]}"

        # Main screen clock
        if hasattr(self, "_clock_label"):
            self._clock_label.config(text=time_str)
            self._date_label.config(text=date_str)
            self._greeting_label.config(text=_greeting())

        # Night screen clock
        if hasattr(self, "_night_clock"):
            self._night_clock.config(text=time_str)
            self._night_date.config(text=date_str)

        # Update weather on main screen
        if self.weather and hasattr(self, "_weather_label"):
            wd = self.weather.data
            if wd:
                icon = _weather_icon(wd.get("icon", ""))
                self._weather_label.config(
                    text=f"{icon} {wd['temp']}\u00B0C  {wd['description']}")

        # Update alerts
        self._update_alerts()

        if self.root:
            self.root.after(1000, self._update_clock)

    def _update_alerts(self):
        if not self.db or not hasattr(self, "_alert_left"):
            return

        # Left alert: medication status
        today = datetime.now().strftime("%Y-%m-%d")
        pending_count = 0
        for user in self._users:
            meds = self.db.get_medications(user["id"], active_only=True)
            log_today = self.db.get_medication_log(user["id"], date=today)
            taken_ids = {e["medication_id"] for e in log_today}
            pending_count += sum(1 for m in meds if m["id"] not in taken_ids)

        if pending_count > 0:
            self._alert_left.config(
                text=f"\U0001F48A {pending_count} medicamento(s) pendiente(s)",
                fg=WARNING,
            )
        else:
            self._alert_left.config(text="\u2714 Medicamentos al dia", fg=SUCCESS)

        # Right alert: next reminder
        try:
            all_rem = self.db.get_all_active_reminders()
            now_str = datetime.now().strftime("%H:%M")
            upcoming = [r for r in all_rem if r["remind_at"] >= now_str]
            if upcoming:
                r = upcoming[0]
                self._alert_right.config(
                    text=f"\u23F0 {r['remind_at']} - {r['text']}",
                    fg=ACCENT,
                )
            else:
                self._alert_right.config(text="", fg=ACCENT)
        except Exception:
            pass

    def _refresh_main_screen(self):
        """Refresh dynamic content on main screen."""
        self._update_alerts()
        if self.weather and hasattr(self, "_weather_label"):
            wd = self.weather.data
            if wd:
                icon = _weather_icon(wd.get("icon", ""))
                self._weather_label.config(
                    text=f"{icon} {wd['temp']}\u00B0C  {wd['description']}")

    # ===== QUEUE PROCESSING =====

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
            text = msg["text"]
            color = msg.get("color", SUCCESS)
            # Update conversation screen
            if hasattr(self, "_conv_status"):
                self._conv_status.config(text=text)
            # Update main screen status
            if hasattr(self, "_status_label"):
                self._status_label.config(text=f"Maya: {text}")
                self._status_dot.config(fg=color)

        elif kind == "transcript":
            if hasattr(self, "_conv_transcript"):
                self._conv_transcript.config(text=msg["text"])

        elif kind == "response":
            if hasattr(self, "_conv_response"):
                self._conv_response.config(text=msg["text"])

        elif kind == "reminders":
            if hasattr(self, "_conv_reminders"):
                self._conv_reminders.config(text=msg["text"])

        elif kind == "user":
            name = msg.get("name", "")
            if hasattr(self, "_conv_title"):
                self._conv_title.config(text=f"Maya - {name}")

        elif kind == "show_conversation":
            self._show_screen("conversation")

        elif kind == "show_main":
            self._go_home()

        elif kind == "_config_bt":
            if hasattr(self, "_bt_status_label"):
                self._bt_status_label.config(
                    text=msg["text"], fg=msg.get("color", TEXT_SEC))

        elif kind == "_config_wifi":
            if hasattr(self, "_wifi_status_label"):
                self._wifi_status_label.config(text=msg["text"])
            if hasattr(self, "_wifi_ip_label"):
                self._wifi_ip_label.config(text=msg.get("ip", ""))

        elif kind == "_config_sys":
            if hasattr(self, "_sys_info_label"):
                self._sys_info_label.config(text=msg["text"])

        elif kind == "_wifi_scan_done":
            if hasattr(self, "_wifi_dialog_listbox"):
                lb = self._wifi_dialog_listbox
                lb.delete(0, tk.END)
                if "error" in msg:
                    self._wifi_dialog_status.config(text=f"Error: {msg['error']}")
                else:
                    for net in msg["networks"]:
                        lb.insert(tk.END, net)
                    self._wifi_dialog_status.config(
                        text=f"{len(msg['networks'])} redes encontradas")

        elif kind == "_wifi_connect_result":
            if hasattr(self, "_wifi_dialog_status"):
                self._wifi_dialog_status.config(text=msg["text"])

        elif kind == "talk_btn":
            if hasattr(self, "_conv_talk_btn"):
                if msg.get("enabled", True):
                    self._conv_talk_btn.config(
                        state="normal", bg=SUCCESS, text="Toca para\nhablar")
                else:
                    self._conv_talk_btn.config(
                        state="disabled", bg="#88BB88", text="Procesando...")

    # ===== EVENT HANDLERS =====

    def _on_talk_pressed(self):
        if self.on_talk:
            threading.Thread(target=self.on_talk, daemon=True).start()

    def _on_close_pressed(self):
        if self.on_close:
            self.on_close()
        self.stop()

    # ===== PUBLIC API (thread-safe) =====

    def set_status(self, text: str, color: str = SUCCESS):
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

    def show_conversation(self):
        self.queue.put({"type": "show_conversation"})

    def show_main(self):
        self.queue.put({"type": "show_main"})

    def stop(self):
        self._running = False
        if self.root:
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass

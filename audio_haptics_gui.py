# TrueGear ME02 – Audio-to-Haptics GUI (modern)
# Braucht audio_haptics.py im selben Ordner.
# Installation (einmalig):  pip install customtkinter
# Start:                    python audio_haptics_gui.py

import json
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import numpy as np
import pyaudiowpatch as pyaudio

import audio_haptics as ah

# Profile im Benutzerordner speichern (im Programmordner darf man oft nicht schreiben)
_PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AudioHaptics")
os.makedirs(_PROFILE_DIR, exist_ok=True)
PROFILE_FILE = os.path.join(_PROFILE_DIR, "profiles.json")

# ---- Design-Token
BG      = "#161B24"   # Fläche: dunkles Schieferblau
PANEL   = "#1E2532"   # Karten/Panels
LINE    = "#2B3445"   # Trennlinien, ruhende Motoren
TEXT    = "#E6EAF0"
MUTED   = "#8A94A6"
EMBER   = "#F08A4B"   # der einzige Akzent: Haptik/Glut
EMBER_D = "#B8623A"   # Akzent gedimmt (Hover)
FONT    = "Segoe UI"

SLIDERS = [
    ("gate",           "Schwelle",          0.0,  1.0,  0.005),
    ("gain",           "Gain",              0.5,  20.0, 0.5),
    ("max_intensity",  "Stärke Impuls",     10,   100,  5),
    ("cont_intensity", "Stärke Dauerbass",  0,    100,  5),
    ("attack_ms",      "Attack",            1,    50,   1),
    ("release_ms",     "Release",           20,   500,  10),
    ("cooldown_ms",    "Cooldown",          30,   400,  10),
    ("low_hz",         "Bass von",          15,   80,   5),
    ("high_hz",        "Bass bis",          60,   250,  5),
]
UNITS = {"attack_ms": "ms", "release_ms": "ms", "cooldown_ms": "ms", "low_hz": "Hz", "high_hz": "Hz"}


def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


class VestMap(tk.Canvas):
    """Live-Karte der 40 Motoren: vorne links, hinten rechts. Glüht beim Ansteuern."""

    CELL, GAP = 26, 6

    def __init__(self, master):
        w = 2 * (4 * (self.CELL + self.GAP)) + 70
        h = 5 * (self.CELL + self.GAP) + 44
        super().__init__(master, width=w, height=h, bg=PANEL, highlightthickness=0)
        self.cells = {}
        for side, x0, title in (("front", 20, "Vorne"), ("back", 20 + 4 * (self.CELL + self.GAP) + 40, "Hinten")):
            self.create_text(x0 + 2 * (self.CELL + self.GAP) - self.GAP / 2, 14, text=title,
                             fill=MUTED, font=(FONT, 11))
            for r in range(5):
                for c in range(4):
                    x = x0 + c * (self.CELL + self.GAP)
                    y = 30 + r * (self.CELL + self.GAP)
                    self.cells[(side, r, c)] = self.create_rectangle(
                        x, y, x + self.CELL, y + self.CELL, fill=LINE, outline="")
        # kleine Hilfe: L/R aus Trägersicht
        self.create_text(12, h - 10, text="L", fill=MUTED, font=(FONT, 9))
        self.create_text(20 + 4 * (self.CELL + self.GAP) - 8, h - 10, text="R", fill=MUTED, font=(FONT, 9))

    def show(self, left, right, core_only):
        for (side, r, c), item in self.cells.items():
            if core_only and r >= 3:
                t = 0.0
            else:
                t = (left if c < 2 else right) / 100.0
            self.itemconfig(item, fill=lerp_color(LINE, EMBER, t))


# Hilfe: Reiter -> Liste von (Titel, Text). Titel "" = einfacher Absatz.
HELP = {
    "Erste Schritte": [
        ("TrueGear Player starten", "Der Player muss laufen und die Weste verbunden sein (Status im Player grün). "
         "Diese App steuert die Weste nicht direkt, sondern schickt ihre Befehle an den Player."),
        ("Verbindung prüfen", "Oben rechts steht „Player verbunden“. Mit „Test-Vibration“ spürst du sofort, ob alles steht."),
        ("Audio-Gerät wählen", "Das Wiedergabegerät, auf dem du hörst – Kopfhörer oder Boxen. Die App hört nur mit, "
         "der Ton wird nicht verändert."),
        ("Start drücken", "Spiel, Video oder Musik abspielen. Bass wird erkannt und in Vibration übersetzt."),
    ],
    "Bedienung": [
        ("Start / Stop", "Mithören starten oder beenden."),
        ("Test-Vibration", "Kurzer Impuls auf allen Motoren, um die Verbindung zu prüfen."),
        ("Audio-Gerät / Neu laden", "Gerät wechseln oder Liste aktualisieren, wenn du etwas umgesteckt hast."),
        ("Profil", "Fertige Voreinstellungen. „Speichern“ überschreibt das gewählte Profil mit den aktuellen Reglern, "
         "„Neu…“ legt ein eigenes an. Profile liegen in %LOCALAPPDATA%\\AudioHaptics\\profiles.json."),
        ("Sendetakt", "Wie oft pro Sekunde Befehle an die Weste gehen. Vibriert die Weste nach dem Stoppen noch nach, "
         "einen größeren Wert wählen."),
        ("Dauerbass", "An: tiefes Grollen erzeugt durchgehende Vibration, Stärke folgt der Lautstärke. "
         "Aus: nur kurze Impulse bei Bass-Schlägen wie Explosionen oder Schüssen."),
    ],
    "Parameter": [
        ("Schwelle", "Ab welcher Bass-Lautstärke reagiert wird. Höher = leise Bässe und Hintergrund werden ignoriert. "
         "Reagiert die Weste ständig, Schwelle erhöhen."),
        ("Gain", "Verstärkung oberhalb der Schwelle. Höher = schon mittlere Bässe erreichen volle Stärke."),
        ("Stärke Impuls", "Maximale Vibration bei Bass-Schlägen (0–100)."),
        ("Stärke Dauerbass", "Maximale Vibration bei durchgehendem Bass."),
        ("Attack", "Wie schnell auf einen Anstieg reagiert wird. Kleiner = direkter."),
        ("Release", "Wie schnell die Vibration nach dem Bass abklingt."),
        ("Cooldown", "Mindestabstand zwischen zwei Impulsen, verhindert Dauerfeuer bei schnellen Bass-Folgen."),
        ("Bass von / bis", "Ausgewerteter Frequenzbereich. 20–120 Hz sind Subbass und Bass. „Bis“ höher setzen, "
         "wenn Schüsse zu wenig auslösen."),
    ],
    "Tipps": [
        ("Westenkarte", "Zeigt live, welche Motoren angesteuert werden – links/rechts nach Stereo-Bild, "
         "vorne und hinten."),
        ("Verzögerung", "Ein kleiner Versatz zwischen Ton und Vibration ist normal. Er entsteht im TrueGear Player "
         "und auf der Funkstrecke zur Weste, nicht in dieser App. Adapter frei auf den Tisch legen hilft."),
        ("Startpunkt", "Profil „balanced“ ist ein guter Anfang. Zu viel Vibration: Schwelle hoch. "
         "Zu wenig: Gain hoch oder „Bass bis“ auf 150 Hz."),
        ("", "Dieses Projekt ist ein Community-Tool und wird nicht von TrueGear hergestellt oder unterstützt."),
    ],
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Audio Haptics für TrueGear ME02")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self._set_icon()

        self.profiles = {k: dict(v) for k, v in ah.PROFILES.items()}
        self._load_profiles()
        self.prof = dict(self.profiles["balanced"])
        self.tg = None
        self.engine = None
        self.pa = None
        self.stream = None
        self.status = {"level": 0.0, "inten": 0, "hit_until": 0.0}
        self.vars = {}
        self.val_labels = {}

        self._build()
        self._refresh_devices()
        self._apply_profile_to_sliders()
        self.after(50, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_icon(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, "icon.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
                self.after(250, lambda: self.iconbitmap(ico))  # CustomTkinter setzt sonst sein eigenes Icon
            except Exception:
                pass

    # ------------------------------------------------------------ Aufbau
    def _panel(self, master):
        return ctk.CTkFrame(master, fg_color=PANEL, corner_radius=14)

    def _build(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)

        # Kopfzeile
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(18, 8))
        ctk.CTkLabel(head, text="Audio Haptics", font=(FONT, 22, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(head, text="TrueGear ME02 · inoffiziell", font=(FONT, 12), text_color=MUTED).pack(side="left", padx=12, pady=(6, 0))
        self.lbl_conn = ctk.CTkLabel(head, text="● Player nicht verbunden", font=(FONT, 12), text_color=MUTED)
        self.lbl_conn.pack(side="right")
        ctk.CTkButton(head, text="Hilfe", width=70, height=30, corner_radius=8, fg_color=LINE,
                      hover_color="#374158", text_color=TEXT, font=(FONT, 12),
                      command=self._show_help).pack(side="right", padx=(0, 14))

        # Linke Spalte: Steuerung
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="n", padx=(20, 10), pady=(0, 20))

        ctrl = self._panel(left); ctrl.pack(fill="x")
        row = ctk.CTkFrame(ctrl, fg_color="transparent"); row.pack(fill="x", padx=14, pady=14)
        self.btn_start = ctk.CTkButton(row, text="Start", width=120, height=40, corner_radius=10,
                                       font=(FONT, 14, "bold"), fg_color=EMBER, hover_color=EMBER_D,
                                       text_color="#1A1A1A", command=self._toggle)
        self.btn_start.pack(side="left")
        ctk.CTkButton(row, text="Test-Vibration", width=130, height=40, corner_radius=10, font=(FONT, 13),
                      fg_color=LINE, hover_color="#374158", text_color=TEXT, command=self._test).pack(side="left", padx=10)

        grid = ctk.CTkFrame(ctrl, fg_color="transparent"); grid.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(grid, text="Audio-Gerät", font=(FONT, 12), text_color=MUTED).grid(row=0, column=0, sticky="w")
        self.cb_device = ctk.CTkOptionMenu(grid, width=300, values=["–"], fg_color=BG, button_color=LINE,
                                           button_hover_color="#374158", font=(FONT, 12), text_color=TEXT)
        self.cb_device.grid(row=1, column=0, sticky="w", pady=(2, 8))
        ctk.CTkButton(grid, text="Neu laden", width=90, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._refresh_devices).grid(row=1, column=1, padx=8, pady=(2, 8))

        ctk.CTkLabel(grid, text="Profil", font=(FONT, 12), text_color=MUTED).grid(row=2, column=0, sticky="w")
        self.cb_profile = ctk.CTkOptionMenu(grid, width=300, values=list(self.profiles.keys()), fg_color=BG,
                                            button_color=LINE, button_hover_color="#374158", font=(FONT, 12),
                                            text_color=TEXT, command=lambda v: self._select_profile())
        self.cb_profile.set("balanced")
        self.cb_profile.grid(row=3, column=0, sticky="w", pady=(2, 0))
        pb = ctk.CTkFrame(grid, fg_color="transparent"); pb.grid(row=3, column=1, padx=8, pady=(2, 0))
        ctk.CTkButton(pb, text="Speichern", width=90, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._save_profile).pack(side="left")
        ctk.CTkButton(pb, text="Neu…", width=60, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._save_as).pack(side="left", padx=(6, 0))

        opts = ctk.CTkFrame(ctrl, fg_color="transparent"); opts.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(opts, text="Sendetakt", font=(FONT, 12), text_color=MUTED).pack(side="left")
        self.cb_rate = ctk.CTkOptionMenu(opts, width=100, values=[f"{int(r*1000)} ms" for r in ah.RATES],
                                         fg_color=BG, button_color=LINE, button_hover_color="#374158",
                                         font=(FONT, 12), text_color=TEXT, command=self._rate_changed)
        self.cb_rate.set(f"{int(ah.RATES[ah.RATE['i']]*1000)} ms")
        self.cb_rate.pack(side="left", padx=8)
        self.var_cont = tk.BooleanVar(value=True)
        ctk.CTkSwitch(opts, text="Dauerbass", variable=self.var_cont, progress_color=EMBER,
                      font=(FONT, 12), text_color=TEXT, command=self._sliders_changed).pack(side="left", padx=14)

        # Regler
        sl = self._panel(left); sl.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(sl, text="Parameter", font=(FONT, 13, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        for i, (key, label, lo, hi, step) in enumerate(SLIDERS, start=1):
            ctk.CTkLabel(sl, text=label, width=120, anchor="w", font=(FONT, 12), text_color=MUTED).grid(row=i, column=0, sticky="w", padx=(14, 4), pady=3)
            var = tk.DoubleVar(); self.vars[key] = var
            s = ctk.CTkSlider(sl, from_=lo, to=hi, variable=var, width=240, progress_color=EMBER,
                              button_color=EMBER, button_hover_color=EMBER_D, fg_color=LINE,
                              number_of_steps=int(round((hi - lo) / step)),
                              command=lambda v, k=key: self._sliders_changed())
            s.grid(row=i, column=1, padx=4, pady=3)
            lab = ctk.CTkLabel(sl, text="", width=70, anchor="e", font=(FONT, 12), text_color=TEXT)
            lab.grid(row=i, column=2, padx=(4, 14), pady=3)
            self.val_labels[key] = lab
            var.trace_add("write", lambda *a, k=key: self.val_labels[k].configure(text=self._fmt(k)))
        ctk.CTkLabel(sl, text="", height=4).grid(row=len(SLIDERS) + 1, column=0)

        # Rechte Spalte: Echtzeit
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="n", padx=(10, 20), pady=(0, 20))
        live = self._panel(right); live.pack(fill="x")
        ctk.CTkLabel(live, text="Live", font=(FONT, 13, "bold"), text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 6))
        self.vest = VestMap(live); self.vest.pack(padx=14, pady=(0, 10))

        for name, attr in (("Bass-Pegel", "pb_bass"), ("Haptik-Stärke", "pb_hap")):
            ctk.CTkLabel(live, text=name, font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=14)
            bar = ctk.CTkProgressBar(live, width=300, height=10, progress_color=EMBER, fg_color=LINE)
            bar.set(0); bar.pack(anchor="w", padx=14, pady=(2, 10))
            setattr(self, attr, bar)
        self.lbl_hit = ctk.CTkLabel(live, text=" ", font=(FONT, 12, "bold"), text_color=EMBER)
        self.lbl_hit.pack(anchor="w", padx=14, pady=(0, 12))

        self.lbl_status = ctk.CTkLabel(right, text="Bereit.", font=(FONT, 12), text_color=MUTED, wraplength=320, justify="left")
        self.lbl_status.pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------ Regler
    def _fmt(self, key):
        v = self.vars[key].get()
        if key == "gate":
            return f"{v:.3f}"
        if key == "gain":
            return f"{v:.1f}"
        return f"{int(v)} {UNITS.get(key, '')}".strip()

    def _sliders_changed(self):
        old = (self.prof["low_hz"], self.prof["high_hz"])
        for key, *_ in SLIDERS:
            v = self.vars[key].get()
            self.prof[key] = round(v, 3) if key in ("gate", "gain") else int(v)
        if self.prof["high_hz"] <= self.prof["low_hz"] + 10:
            self.prof["high_hz"] = self.prof["low_hz"] + 10
            self.vars["high_hz"].set(self.prof["high_hz"])
        self.prof["continuous"] = bool(self.var_cont.get())
        if self.engine and old != (self.prof["low_hz"], self.prof["high_hz"]):
            self.engine.set_profile(self.prof)

    def _apply_profile_to_sliders(self):
        for key, *_ in SLIDERS:
            self.vars[key].set(self.prof[key])
        self.var_cont.set(bool(self.prof.get("continuous", True)))

    def _select_profile(self):
        self.prof.clear(); self.prof.update(self.profiles[self.cb_profile.get()])
        self._apply_profile_to_sliders()
        if self.engine:
            self.engine.set_profile(self.prof)

    def _rate_changed(self, value):
        ah.RATE["i"] = [f"{int(r*1000)} ms" for r in ah.RATES].index(value)

    # ------------------------------------------------------------ Profile
    def _load_profiles(self):
        if os.path.exists(PROFILE_FILE):
            try:
                with open(PROFILE_FILE, encoding="utf-8") as f:
                    for k, v in json.load(f).items():
                        base = dict(ah.PROFILES["balanced"]); base.update(v)
                        self.profiles[k] = base
            except Exception as e:
                print("profiles.json konnte nicht gelesen werden:", e)

    def _write_profiles(self):
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.profiles, f, indent=2, ensure_ascii=False)

    def _save_profile(self):
        name = self.cb_profile.get()
        self.profiles[name] = dict(self.prof)
        self._write_profiles()
        self.lbl_status.configure(text=f"Profil „{name}“ gespeichert.")

    def _save_as(self):
        dlg = ctk.CTkInputDialog(text="Name für das neue Profil:", title="Neues Profil")
        name = (dlg.get_input() or "").strip()
        if not name:
            return
        self.profiles[name] = dict(self.prof)
        self._write_profiles()
        self.cb_profile.configure(values=list(self.profiles.keys()))
        self.cb_profile.set(name)
        self.lbl_status.configure(text=f"Profil „{name}“ angelegt.")

    # ------------------------------------------------------------ Audio
    def _refresh_devices(self):
        pa = pyaudio.PyAudio()
        self.devices = []
        default = ""
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
            self.devices = list(pa.get_loopback_device_info_generator())
        finally:
            pa.terminate()
        names = [d["name"] for d in self.devices] or ["– kein Gerät –"]
        self.cb_device.configure(values=names)
        pick = next((n for n in names if default and default in n), names[0])
        self.cb_device.set(pick)

    def _toggle(self):
        self._stop() if self.stream else self._start()

    def _start(self):
        if not self.devices:
            messagebox.showerror("Kein Gerät", "Kein WASAPI-Loopback-Gerät gefunden.")
            return
        dev = self.devices[[d["name"] for d in self.devices].index(self.cb_device.get())]
        rate = int(dev["defaultSampleRate"])
        ch = max(1, int(dev["maxInputChannels"]))
        self.tg = self.tg or ah.TrueGear()
        self.engine = ah.BassEngine(self.prof, rate, ch)

        def cb(in_data, frame_count, time_info, flags):
            block = np.frombuffer(in_data, dtype=np.float32).reshape(-1, ch)
            level, inten, hit = self.engine.process(block, self.tg)
            self.status["level"], self.status["inten"] = level, inten
            if hit:
                self.status["hit_until"] = time.time() + 0.15
            return (None, pyaudio.paContinue)

        try:
            self.pa = pyaudio.PyAudio()
            self.stream = self.pa.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True,
                                       input_device_index=dev["index"], frames_per_buffer=256,
                                       stream_callback=cb)
            self.stream.start_stream()
        except Exception as e:
            self._stop()
            messagebox.showerror("Audio-Fehler", str(e))
            return
        self.btn_start.configure(text="Stop")
        self.lbl_status.configure(text=f"Läuft: {dev['name']} · {rate} Hz · {ch} Kanäle")

    def _stop(self):
        if self.stream:
            try:
                self.stream.stop_stream(); self.stream.close()
            except Exception:
                pass
        if self.pa:
            self.pa.terminate()
        self.stream = self.pa = self.engine = None
        if self.tg:
            self.tg.stop_all()
        self.btn_start.configure(text="Start")
        self.lbl_status.configure(text="Gestoppt.")

    def _test(self):
        self.tg = self.tg or ah.TrueGear()
        inten = int(self.prof["max_intensity"])
        self.tg.pulse(inten, inten, 120)
        self.vest.show(inten, inten, False)
        self.after(150, lambda: self.vest.show(0, 0, False))

    # ------------------------------------------------------------ Hilfe
    def _show_help(self):
        if getattr(self, "_help_win", None) and self._help_win.winfo_exists():
            self._help_win.lift(); return
        win = ctk.CTkToplevel(self)
        win.title("Hilfe – Audio Haptics")
        win.geometry("680x600")
        win.configure(fg_color=BG)
        self._help_win = win
        ctk.CTkLabel(win, text="So benutzt du Audio Haptics", font=(FONT, 20, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(win, text="Bass aus jedem Spiel spürbar machen – über den TrueGear Player.",
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 10))
        tabs = ctk.CTkTabview(win, fg_color=PANEL, corner_radius=14, segmented_button_fg_color=BG,
                              segmented_button_selected_color=EMBER, segmented_button_selected_hover_color=EMBER_D,
                              segmented_button_unselected_color=BG, segmented_button_unselected_hover_color=LINE,
                              text_color=TEXT)
        tabs.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        for tab, items in HELP.items():
            frame = ctk.CTkScrollableFrame(tabs.add(tab), fg_color="transparent")
            frame.pack(fill="both", expand=True)
            for title, text in items:
                block = ctk.CTkFrame(frame, fg_color="transparent")
                block.pack(fill="x", padx=8, pady=(6, 8))
                if title:
                    ctk.CTkLabel(block, text=title, font=(FONT, 13, "bold"), text_color=EMBER,
                                 anchor="w").pack(anchor="w")
                ctk.CTkLabel(block, text=text, font=(FONT, 12), text_color=TEXT, anchor="w",
                             justify="left", wraplength=560).pack(anchor="w", pady=(1, 0))
        win.after(100, win.lift)

    # ------------------------------------------------------------ Anzeige
    def _tick(self):
        self.pb_bass.set(self.status["level"])
        self.pb_hap.set(self.status["inten"] / 100.0)
        self.lbl_hit.configure(text="Impuls" if time.time() < self.status["hit_until"] else " ")
        if self.engine:
            li, ri, core, t = self.engine.last_lr
            age = time.time() - t
            fade = max(0.0, 1.0 - age / 0.18)           # Glühen klingt in 180 ms ab
            self.vest.show(li * fade, ri * fade, core)
        if self.tg:
            ok = self.tg.ws is not None
            self.lbl_conn.configure(text="● Player verbunden" if ok else "● Player nicht erreichbar",
                                    text_color=EMBER if ok else MUTED)
        self.after(50, self._tick)

    def _on_close(self):
        self._stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()

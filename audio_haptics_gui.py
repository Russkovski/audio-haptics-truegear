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
SETTINGS_PATH = os.path.join(_PROFILE_DIR, "settings.json")

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
EMS_SLIDERS = [
    ("ems_intensity",   0,    100,  1),
    ("ems_cont_intensity", 0, 100,  1),
    ("ems_threshold",   0.0,  1.0,  0.005), # roher Bass-Pegel, gleiche Skala wie "Schwelle"
    ("ems_cooldown_ms", 500,  5000, 100),
]
UNITS = {"ems_cooldown_ms": "ms", "attack_ms": "ms", "release_ms": "ms", "cooldown_ms": "ms", "low_hz": "Hz", "high_hz": "Hz"}


def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))


class VestMap(tk.Canvas):
    """Live-Karte der 40 Motoren: vorne links, hinten rechts. Glüht beim Ansteuern."""

    CELL, GAP = 26, 6

    def __init__(self, master, front="Front", back="Back", cuff="EMS"):
        self.OFF = 34  # Platz links/rechts für die EMS-Bänder
        w = 2 * (4 * (self.CELL + self.GAP)) + 70 + 2 * self.OFF
        h = 5 * (self.CELL + self.GAP) + 44
        super().__init__(master, width=w, height=h, bg=PANEL, highlightthickness=0)
        self.cells = {}
        # EMS-Armbänder: schmale Balken links und rechts neben der Weste
        self.cuffs = []
        cy0, cy1 = 30 + 1 * (self.CELL + self.GAP), 30 + 3 * (self.CELL + self.GAP) - self.GAP
        for x in (8, w - 8 - 14):
            self.cuffs.append(self.create_rectangle(x, cy0, x + 14, cy1, fill=LINE, outline=""))
            self.create_text(x + 7, cy1 + 12, text=cuff, fill=MUTED, font=(FONT, 8))
        for side, x0, title in (("front", 20 + self.OFF, front), ("back", 20 + self.OFF + 4 * (self.CELL + self.GAP) + 40, back)):
            self.create_text(x0 + 2 * (self.CELL + self.GAP) - self.GAP / 2, 14, text=title,
                             fill=MUTED, font=(FONT, 11))
            for r in range(5):
                for c in range(4):
                    x = x0 + c * (self.CELL + self.GAP)
                    y = 30 + r * (self.CELL + self.GAP)
                    self.cells[(side, r, c)] = self.create_rectangle(
                        x, y, x + self.CELL, y + self.CELL, fill=LINE, outline="")
        # kleine Hilfe: L/R aus Trägersicht
        self.create_text(12 + self.OFF, h - 10, text="L", fill=MUTED, font=(FONT, 9))
        self.create_text(20 + self.OFF + 4 * (self.CELL + self.GAP) - 8, h - 10, text="R", fill=MUTED, font=(FONT, 9))

    def show_ems(self, t_left, t_right):
        self.itemconfig(self.cuffs[0], fill=lerp_color(LINE, EMBER, t_left))
        self.itemconfig(self.cuffs[1], fill=lerp_color(LINE, EMBER, t_right))

    def show(self, q, core_only, fade=1.0):
        # q: {"fl","fr","bl","br"} 0..100
        for (side, r, c), item in self.cells.items():
            if core_only and r >= 3:
                t = 0.0
            else:
                k = ("f" if side == "front" else "b") + ("l" if c < 2 else "r")
                t = q.get(k, 0) * fade / 100.0
            self.itemconfig(item, fill=lerp_color(LINE, EMBER, t))


# ---- Sprachen. Standard Englisch, Umschalter in der Kopfzeile, Wahl wird gespeichert.
SETTINGS_FILE = None  # wird unten gesetzt (gleicher Ordner wie profiles.json)

STR = {
    "en": {
        "subtitle": "TrueGear ME02 · unofficial", "help": "Help",
        "conn_no": "● Player not connected", "conn_ok": "● Player connected", "conn_lost": "● Player not reachable",
        "start": "Start", "stop": "Stop", "test": "Test vibration", "test_ems": "Test EMS (20)", "cuff": "EMS",
        "device": "Audio device", "reload": "Reload", "profile": "Profile", "save": "Save", "new": "New…",
        "rate": "Send rate", "cont": "Continuous bass", "params": "Parameters", "live": "Live",
        "bass": "Bass level", "haptic": "Haptic strength", "peak": "Peak (raw)", "impulse": "Impulse",
        "ready": "Ready.", "stopped": "Stopped.", "running": "Running: {dev} · {rate} Hz · {ch} ch",
        "saved": "Profile “{name}” saved.", "created": "Profile “{name}” created.",
        "new_title": "New profile", "new_prompt": "Name for the new profile:",
        "no_dev_title": "No device", "no_dev": "No WASAPI loopback device found.",
        "audio_err": "Audio error", "front": "Front", "back": "Back",
        "ems": "EMS arm cuffs", "ems_on": "EMS on hits", "ems_cont_on": "EMS continuous", "ems_hint": "100 % = the EMS strength set in the TrueGear Player; adjust the base level there. On hits: strong single bass hits, with threshold and min. gap. Continuous: follows the bass level like the vest.",
        "ems_warn_title": "Enable EMS?",
        "ems_warn": "The EMS cuffs deliver real electrical stimulation. Start low (10), raise slowly, and stop if it feels uncomfortable. "
                    "Do not use if you have a pacemaker, heart condition, epilepsy or are pregnant. Enable at your own risk.",
        "ems_fired": "EMS",
        "help_title": "How to use Audio Haptics",
        "help_sub": "Feel the bass of any game – through the TrueGear Player.",
        "sl": {"gate": "Threshold", "gain": "Gain", "max_intensity": "Impulse strength",
               "cont_intensity": "Continuous strength", "attack_ms": "Attack", "release_ms": "Release",
               "cooldown_ms": "Cooldown", "low_hz": "Bass from", "high_hz": "Bass to",
               "ems_intensity": "EMS strength", "ems_cont_intensity": "EMS continuous", "ems_threshold": "EMS threshold", "ems_cooldown_ms": "EMS min. gap"},
    },
    "de": {
        "subtitle": "TrueGear ME02 · inoffiziell", "help": "Hilfe",
        "conn_no": "● Player nicht verbunden", "conn_ok": "● Player verbunden", "conn_lost": "● Player nicht erreichbar",
        "start": "Start", "stop": "Stop", "test": "Test-Vibration", "test_ems": "Test EMS (20)", "cuff": "EMS",
        "device": "Audio-Gerät", "reload": "Neu laden", "profile": "Profil", "save": "Speichern", "new": "Neu…",
        "rate": "Sendetakt", "cont": "Dauerbass", "params": "Parameter", "live": "Live",
        "bass": "Bass-Pegel", "haptic": "Haptik-Stärke", "peak": "Peak (roh)", "impulse": "Impuls",
        "ready": "Bereit.", "stopped": "Gestoppt.", "running": "Läuft: {dev} · {rate} Hz · {ch} Kanäle",
        "saved": "Profil „{name}“ gespeichert.", "created": "Profil „{name}“ angelegt.",
        "new_title": "Neues Profil", "new_prompt": "Name für das neue Profil:",
        "no_dev_title": "Kein Gerät", "no_dev": "Kein WASAPI-Loopback-Gerät gefunden.",
        "audio_err": "Audio-Fehler", "front": "Vorne", "back": "Hinten",
        "ems": "EMS-Armbänder", "ems_on": "EMS bei Schlägen", "ems_cont_on": "EMS dauerhaft", "ems_hint": "100 % = die im TrueGear Player eingestellte EMS-Stärke; den Grundwert dort einstellen. Bei Schlägen: kräftige Einzelschläge, mit Schwelle und Abstand. Dauerhaft: folgt wie die Weste dem Bass-Pegel.",
        "ems_warn_title": "EMS einschalten?",
        "ems_warn": "Die EMS-Bänder geben echte elektrische Reize ab. Niedrig anfangen (10), langsam steigern, bei Unbehagen sofort abschalten. "
                    "Nicht verwenden bei Herzschrittmacher, Herzerkrankungen, Epilepsie oder Schwangerschaft. Nutzung auf eigene Gefahr.",
        "ems_fired": "EMS",
        "help_title": "So benutzt du Audio Haptics",
        "help_sub": "Bass aus jedem Spiel spürbar machen – über den TrueGear Player.",
        "sl": {"gate": "Schwelle", "gain": "Gain", "max_intensity": "Stärke Impuls",
               "cont_intensity": "Stärke Dauerbass", "attack_ms": "Attack", "release_ms": "Release",
               "cooldown_ms": "Cooldown", "low_hz": "Bass von", "high_hz": "Bass bis",
               "ems_intensity": "EMS-Stärke", "ems_cont_intensity": "EMS dauerhaft", "ems_threshold": "EMS-Schwelle", "ems_cooldown_ms": "EMS-Abstand"},
    },
}

HELP = {
    "en": {
        "Getting started": [
            ("Start the TrueGear Player", "The Player must be running with the vest connected (status green in the Player). "
             "This app does not talk to the vest directly – it sends its commands to the Player."),
            ("Check the connection", "Top right shows “Player connected”. “Test vibration” lets you feel immediately whether everything works."),
            ("Pick the audio device", "The playback device you listen on – headphones or speakers. The app only listens; your audio is not changed."),
            ("Press Start", "Play a game, video or music. Bass is detected and turned into vibration."),
        ],
        "Controls": [
            ("Start / Stop", "Start or stop listening."),
            ("Test vibration", "Short pulse on all motors to check the connection."),
            ("Audio device / Reload", "Switch device or refresh the list after plugging something in."),
            ("Profile", "Ready-made presets. “Save” overwrites the selected profile with the current sliders, "
             "“New…” creates your own. Profiles are stored in %LOCALAPPDATA%\\AudioHaptics\\profiles.json."),
            ("Send rate", "How often commands go to the vest. If the vest keeps vibrating after you stop audio, choose a slower rate."),
            ("Continuous bass", "On: sustained rumble creates continuous vibration, strength follows loudness. "
             "Off: only short impulses on bass hits such as explosions or shots."),
        ],
        "Parameters": [
            ("Threshold", "Bass level below which nothing triggers. Higher = quiet bass and background are ignored. "
             "If the vest reacts to everything, raise it."),
            ("Gain", "Boost above the threshold. Higher = medium bass already reaches full strength."),
            ("Impulse strength", "Maximum vibration on bass hits (0–100)."),
            ("Continuous strength", "Maximum vibration for sustained bass."),
            ("Attack", "How fast detection reacts to a rise. Smaller = more direct."),
            ("Release", "How fast vibration fades after the bass."),
            ("Cooldown", "Minimum gap between two impulses; prevents machine-gun vibration on fast bass sequences."),
            ("Bass from / to", "Frequency band that is analysed. 20–120 Hz is sub-bass and bass. Raise “to” if gunshots trigger too little."),
        ],
        "EMS cuffs": [
            ("What it does", "Optional. “EMS on hits”: strong single bass hits (explosions, big shots) trigger the cuffs, controlled by EMS threshold and min. gap. "
             "“EMS continuous”: the cuffs follow the bass level like the vest does, using the “EMS continuous” slider – no min. gap applies."),
            ("Safety", "Off by default. Start at strength 10 and raise slowly; 100 % in the app equals the EMS strength you set in the TrueGear Player – the Player value is the base level. "
             "EMS threshold = raw bass level a hit must reach, same scale as “Threshold” – the “Peak” value in the Live panel shows what your explosions actually reach. EMS min. gap = minimum time between two shocks."),
            ("", "Do not use EMS with a pacemaker, heart condition, epilepsy or during pregnancy. Use at your own risk."),
        ],
        "Tips": [
            ("Vest map", "Shows live which motors are driven. Stereo: left/right on front and back. 5.1/7.1: front channels drive the front, rear/side channels the back, LFE everything – set your Windows playback device to 5.1/7.1 to use it."),
            ("Delay", "A small offset between sound and vibration is normal. It comes from the TrueGear Player and the wireless "
             "link to the vest, not from this app. Placing the adapter freely on the desk helps."),
            ("Starting point", "Profile “balanced” is a good start. Too much vibration: raise the threshold. Too little: raise gain or set “Bass to” to 150 Hz."),
            ("", "This is a community tool. It is not made by, affiliated with or supported by TrueGear."),
        ],
    },
    "de": {
        "Erste Schritte": [
            ("TrueGear Player starten", "Der Player muss laufen und die Weste verbunden sein (Status im Player grün). "
             "Diese App steuert die Weste nicht direkt, sondern schickt ihre Befehle an den Player."),
            ("Verbindung prüfen", "Oben rechts steht „Player verbunden“. Mit „Test-Vibration“ spürst du sofort, ob alles steht."),
            ("Audio-Gerät wählen", "Das Wiedergabegerät, auf dem du hörst – Kopfhörer oder Boxen. Die App hört nur mit, der Ton wird nicht verändert."),
            ("Start drücken", "Spiel, Video oder Musik abspielen. Bass wird erkannt und in Vibration übersetzt."),
        ],
        "Bedienung": [
            ("Start / Stop", "Mithören starten oder beenden."),
            ("Test-Vibration", "Kurzer Impuls auf allen Motoren, um die Verbindung zu prüfen."),
            ("Audio-Gerät / Neu laden", "Gerät wechseln oder Liste aktualisieren, wenn du etwas umgesteckt hast."),
            ("Profil", "Fertige Voreinstellungen. „Speichern“ überschreibt das gewählte Profil mit den aktuellen Reglern, "
             "„Neu…“ legt ein eigenes an. Profile liegen in %LOCALAPPDATA%\\AudioHaptics\\profiles.json."),
            ("Sendetakt", "Wie oft pro Sekunde Befehle an die Weste gehen. Vibriert die Weste nach dem Stoppen noch nach, einen größeren Wert wählen."),
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
            ("Bass von / bis", "Ausgewerteter Frequenzbereich. 20–120 Hz sind Subbass und Bass. „Bis“ höher setzen, wenn Schüsse zu wenig auslösen."),
        ],
        "EMS-Bänder": [
            ("Was es macht", "Optional. „EMS bei Schlägen“: kräftige einzelne Bass-Schläge (Explosionen, große Schüsse) lösen die Bänder aus, geregelt über EMS-Schwelle und EMS-Abstand. "
             "„EMS dauerhaft“: die Bänder folgen wie die Weste dem Bass-Pegel, Stärke über „EMS dauerhaft“ – ohne Mindestabstand."),
            ("Sicherheit", "Standardmäßig aus. Bei Stärke 10 anfangen und langsam steigern; 100 % in der App entsprechen der im TrueGear Player eingestellten EMS-Stärke – der Player-Wert ist der Grundwert. "
             "EMS-Schwelle = roher Bass-Pegel, den ein Schlag erreichen muss, gleiche Skala wie „Schwelle“ – der Wert „Peak“ im Live-Bereich zeigt, was deine Explosionen tatsächlich erreichen. EMS-Abstand = Mindestzeit zwischen zwei Reizen."),
            ("", "EMS nicht verwenden bei Herzschrittmacher, Herzerkrankungen, Epilepsie oder Schwangerschaft. Nutzung auf eigene Gefahr."),
        ],
        "Tipps": [
            ("Westenkarte", "Zeigt live, welche Motoren angesteuert werden. Stereo: links/rechts auf vorne und hinten. 5.1/7.1: Front-Kanäle steuern vorne, Rear/Side-Kanäle hinten, LFE alles – dafür das Windows-Wiedergabegerät auf 5.1/7.1 stellen."),
            ("Verzögerung", "Ein kleiner Versatz zwischen Ton und Vibration ist normal. Er entsteht im TrueGear Player "
             "und auf der Funkstrecke zur Weste, nicht in dieser App. Adapter frei auf den Tisch legen hilft."),
            ("Startpunkt", "Profil „balanced“ ist ein guter Anfang. Zu viel Vibration: Schwelle hoch. Zu wenig: Gain hoch oder „Bass bis“ auf 150 Hz."),
            ("", "Dieses Projekt ist ein Community-Tool und wird nicht von TrueGear hergestellt oder unterstützt."),
        ],
    },
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Audio Haptics for TrueGear ME02")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self._set_icon()

        self.profiles = {k: dict(v) for k, v in ah.PROFILES.items()}
        self._load_profiles()
        self.prof = dict(self.profiles["balanced"])
        self.prof_name = "balanced"
        self.tg = None
        self.engine = None
        self.pa = None
        self.stream = None
        self.status = {"level": 0.0, "inten": 0, "hit_until": 0.0}
        self.vars = {}
        self.val_labels = {}
        self.lang = self._load_lang()

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

    # ------------------------------------------------------------ Sprache
    def T(self, key, **kw):
        return STR[self.lang][key].format(**kw) if kw else STR[self.lang][key]

    def _load_lang(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                lang = json.load(f).get("lang", "en")
                return lang if lang in STR else "en"
        except Exception:
            return "en"

    def _set_lang(self, lang):
        if lang == self.lang:
            return
        self.lang = lang
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({"lang": lang}, f)
        except Exception:
            pass
        if getattr(self, "_help_win", None) and self._help_win.winfo_exists():
            self._help_win.destroy()
        for w in self.winfo_children():
            w.destroy()
        self.vars = {}; self.val_labels = {}
        self._build()
        self._refresh_devices()
        self._apply_profile_to_sliders()
        if self.stream:
            self.btn_start.configure(text=self.T("stop"))

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
        ctk.CTkLabel(head, text=self.T("subtitle"), font=(FONT, 12), text_color=MUTED).pack(side="left", padx=12, pady=(6, 0))
        self.lbl_conn = ctk.CTkLabel(head, text=self.T("conn_no"), font=(FONT, 12), text_color=MUTED)
        self.lbl_conn.pack(side="right")
        ctk.CTkButton(head, text=self.T("help"), width=70, height=30, corner_radius=8, fg_color=LINE,
                      hover_color="#374158", text_color=TEXT, font=(FONT, 12),
                      command=self._show_help).pack(side="right", padx=(0, 14))
        lang_sw = ctk.CTkSegmentedButton(head, values=["EN", "DE"], width=90, height=30, font=(FONT, 11),
                                         fg_color=BG, unselected_color=BG, unselected_hover_color=LINE,
                                         selected_color=EMBER, selected_hover_color=EMBER_D, text_color=TEXT,
                                         command=lambda v: self._set_lang(v.lower()))
        lang_sw.set(self.lang.upper())
        lang_sw.pack(side="right", padx=(0, 10))

        # Linke Spalte: Steuerung
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="n", padx=(20, 10), pady=(0, 20))

        ctrl = self._panel(left); ctrl.pack(fill="x")
        row = ctk.CTkFrame(ctrl, fg_color="transparent"); row.pack(fill="x", padx=14, pady=14)
        self.btn_start = ctk.CTkButton(row, text=self.T("start"), width=120, height=40, corner_radius=10,
                                       font=(FONT, 14, "bold"), fg_color=EMBER, hover_color=EMBER_D,
                                       text_color="#1A1A1A", command=self._toggle)
        self.btn_start.pack(side="left")
        ctk.CTkButton(row, text=self.T("test"), width=130, height=40, corner_radius=10, font=(FONT, 13),
                      fg_color=LINE, hover_color="#374158", text_color=TEXT, command=self._test).pack(side="left", padx=10)
        ctk.CTkButton(row, text=self.T("test_ems"), width=120, height=40, corner_radius=10, font=(FONT, 13),
                      fg_color=LINE, hover_color="#374158", text_color=TEXT, command=self._test_ems).pack(side="left")

        grid = ctk.CTkFrame(ctrl, fg_color="transparent"); grid.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(grid, text=self.T("device"), font=(FONT, 12), text_color=MUTED).grid(row=0, column=0, sticky="w")
        self.cb_device = ctk.CTkOptionMenu(grid, width=300, values=["–"], fg_color=BG, button_color=LINE,
                                           button_hover_color="#374158", font=(FONT, 12), text_color=TEXT)
        self.cb_device.grid(row=1, column=0, sticky="w", pady=(2, 8))
        ctk.CTkButton(grid, text=self.T("reload"), width=90, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._refresh_devices).grid(row=1, column=1, padx=8, pady=(2, 8))

        ctk.CTkLabel(grid, text=self.T("profile"), font=(FONT, 12), text_color=MUTED).grid(row=2, column=0, sticky="w")
        self.cb_profile = ctk.CTkOptionMenu(grid, width=300, values=list(self.profiles.keys()), fg_color=BG,
                                            button_color=LINE, button_hover_color="#374158", font=(FONT, 12),
                                            text_color=TEXT, command=lambda v: self._select_profile())
        self.cb_profile.set(self.prof_name)
        self.cb_profile.grid(row=3, column=0, sticky="w", pady=(2, 0))
        pb = ctk.CTkFrame(grid, fg_color="transparent"); pb.grid(row=3, column=1, padx=8, pady=(2, 0))
        ctk.CTkButton(pb, text=self.T("save"), width=90, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._save_profile).pack(side="left")
        ctk.CTkButton(pb, text=self.T("new"), width=60, height=28, fg_color=LINE, hover_color="#374158",
                      text_color=TEXT, font=(FONT, 12), command=self._save_as).pack(side="left", padx=(6, 0))

        opts = ctk.CTkFrame(ctrl, fg_color="transparent"); opts.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(opts, text=self.T("rate"), font=(FONT, 12), text_color=MUTED).pack(side="left")
        self.cb_rate = ctk.CTkOptionMenu(opts, width=100, values=[f"{int(r*1000)} ms" for r in ah.RATES],
                                         fg_color=BG, button_color=LINE, button_hover_color="#374158",
                                         font=(FONT, 12), text_color=TEXT, command=self._rate_changed)
        self.cb_rate.set(f"{int(ah.RATES[ah.RATE['i']]*1000)} ms")
        self.cb_rate.pack(side="left", padx=8)
        self.var_cont = tk.BooleanVar(value=True)
        ctk.CTkSwitch(opts, text=self.T("cont"), variable=self.var_cont, progress_color=EMBER,
                      font=(FONT, 12), text_color=TEXT, command=self._sliders_changed).pack(side="left", padx=14)

        # Regler
        sl = self._panel(left); sl.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(sl, text=self.T("params"), font=(FONT, 13, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        for i, (key, label, lo, hi, step) in enumerate(SLIDERS, start=1):
            ctk.CTkLabel(sl, text=STR[self.lang]["sl"][key], width=120, anchor="w", font=(FONT, 12), text_color=MUTED).grid(row=i, column=0, sticky="w", padx=(14, 4), pady=3)
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

        # EMS-Armbänder
        em = self._panel(left); em.pack(fill="x", pady=(12, 0))
        top = ctk.CTkFrame(em, fg_color="transparent"); top.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(12, 2))
        ctk.CTkLabel(top, text=self.T("ems"), font=(FONT, 13, "bold"), text_color=TEXT).pack(side="left")
        self.var_ems = tk.BooleanVar(value=bool(self.prof.get("ems_enabled", False)))
        self.var_ems_cont = tk.BooleanVar(value=bool(self.prof.get("ems_cont_enabled", False)))
        ctk.CTkSwitch(top, text=self.T("ems_cont_on"), variable=self.var_ems_cont, progress_color=EMBER,
                      font=(FONT, 12), text_color=TEXT, command=lambda: self._ems_toggled(self.var_ems_cont)).pack(side="right")
        ctk.CTkSwitch(top, text=self.T("ems_on"), variable=self.var_ems, progress_color=EMBER,
                      font=(FONT, 12), text_color=TEXT, command=lambda: self._ems_toggled(self.var_ems)).pack(side="right", padx=(0, 14))
        ctk.CTkLabel(em, text=self.T("ems_hint"), font=(FONT, 11), text_color=MUTED, wraplength=420,
                     justify="left").grid(row=1, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 4))
        for i, (key, lo, hi, step) in enumerate(EMS_SLIDERS, start=2):
            ctk.CTkLabel(em, text=STR[self.lang]["sl"][key], width=120, anchor="w", font=(FONT, 12), text_color=MUTED).grid(row=i, column=0, sticky="w", padx=(14, 4), pady=3)
            var = tk.DoubleVar(); self.vars[key] = var
            ctk.CTkSlider(em, from_=lo, to=hi, variable=var, width=240, progress_color=EMBER,
                          button_color=EMBER, button_hover_color=EMBER_D, fg_color=LINE,
                          number_of_steps=int(round((hi - lo) / step)),
                          command=lambda v, k=key: self._sliders_changed()).grid(row=i, column=1, padx=4, pady=3)
            lab = ctk.CTkLabel(em, text="", width=70, anchor="e", font=(FONT, 12), text_color=TEXT)
            lab.grid(row=i, column=2, padx=(4, 14), pady=3)
            self.val_labels[key] = lab
            var.trace_add("write", lambda *a, k=key: self.val_labels[k].configure(text=self._fmt(k)))
        ctk.CTkLabel(em, text="", height=4).grid(row=len(EMS_SLIDERS) + 2, column=0)

        # Rechte Spalte: Echtzeit
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="n", padx=(10, 20), pady=(0, 20))
        live = self._panel(right); live.pack(fill="x")
        ctk.CTkLabel(live, text=self.T("live"), font=(FONT, 13, "bold"), text_color=TEXT).pack(anchor="w", padx=14, pady=(12, 6))
        self.vest = VestMap(live, self.T("front"), self.T("back"), self.T("cuff")); self.vest.pack(padx=14, pady=(0, 10))

        for name, attr in ((self.T("bass"), "pb_bass"), (self.T("haptic"), "pb_hap")):
            ctk.CTkLabel(live, text=name, font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=14)
            bar = ctk.CTkProgressBar(live, width=300, height=10, progress_color=EMBER, fg_color=LINE)
            bar.set(0); bar.pack(anchor="w", padx=14, pady=(2, 10))
            setattr(self, attr, bar)
        self.lbl_peak = ctk.CTkLabel(live, text=self.T("peak") + ": 0.000", font=(FONT, 12), text_color=MUTED)
        self.lbl_peak.pack(anchor="w", padx=14, pady=(0, 4))
        self.lbl_hit = ctk.CTkLabel(live, text=" ", font=(FONT, 12, "bold"), text_color=EMBER)
        self.lbl_hit.pack(anchor="w", padx=14, pady=(0, 12))

        self.lbl_status = ctk.CTkLabel(right, text=self.T("ready"), font=(FONT, 12), text_color=MUTED, wraplength=320, justify="left")
        self.lbl_status.pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------ Regler
    def _fmt(self, key):
        v = self.vars[key].get()
        if key == "gate":
            return f"{v:.3f}"
        if key == "ems_threshold":
            return f"{v:.3f}"
        if key == "gain":
            return f"{v:.1f}"
        return f"{int(v)} {UNITS.get(key, '')}".strip()

    def _sliders_changed(self):
        old = (self.prof["low_hz"], self.prof["high_hz"])
        for key, *_ in SLIDERS + [(k,) for k, *_ in EMS_SLIDERS]:
            v = self.vars[key].get()
            self.prof[key] = round(v, 3) if key in ("gate", "gain", "ems_threshold") else int(v)
        self.prof["ems_enabled"] = bool(self.var_ems.get())
        self.prof["ems_cont_enabled"] = bool(self.var_ems_cont.get())
        if self.prof["high_hz"] <= self.prof["low_hz"] + 10:
            self.prof["high_hz"] = self.prof["low_hz"] + 10
            self.vars["high_hz"].set(self.prof["high_hz"])
        self.prof["continuous"] = bool(self.var_cont.get())
        if self.engine and old != (self.prof["low_hz"], self.prof["high_hz"]):
            self.engine.set_profile(self.prof)

    def _ems_toggled(self, var):
        if var.get() and not getattr(self, "_ems_warned", False):
            if messagebox.askokcancel(self.T("ems_warn_title"), self.T("ems_warn"), icon="warning"):
                self._ems_warned = True
            else:
                var.set(False)
        self._sliders_changed()

    def _apply_profile_to_sliders(self):
        for key, *_ in SLIDERS:
            self.vars[key].set(self.prof[key])
        for key, *_ in EMS_SLIDERS:
            self.vars[key].set(self.prof.get(key, ah.PROFILES["balanced"][key]))
        self.var_cont.set(bool(self.prof.get("continuous", True)))
        self.var_ems.set(bool(self.prof.get("ems_enabled", False)))
        self.var_ems_cont.set(bool(self.prof.get("ems_cont_enabled", False)))

    def _select_profile(self):
        self.prof_name = self.cb_profile.get()
        self.prof.clear(); self.prof.update(self.profiles[self.prof_name])
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
        self.lbl_status.configure(text=self.T("saved", name=name))

    def _save_as(self):
        dlg = ctk.CTkInputDialog(text=self.T("new_prompt"), title=self.T("new_title"))
        name = (dlg.get_input() or "").strip()
        if not name:
            return
        self.profiles[name] = dict(self.prof)
        self._write_profiles()
        self.cb_profile.configure(values=list(self.profiles.keys()))
        self.prof_name = name
        self.cb_profile.set(name)
        self.lbl_status.configure(text=self.T("created", name=name))

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
        names = [d["name"] for d in self.devices] or ["–"]
        self.cb_device.configure(values=names)
        pick = next((n for n in names if default and default in n), names[0])
        self.cb_device.set(pick)

    def _toggle(self):
        self._stop() if self.stream else self._start()

    def _start(self):
        if not self.devices:
            messagebox.showerror(self.T("no_dev_title"), self.T("no_dev"))
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
            messagebox.showerror(self.T("audio_err"), str(e))
            return
        self.btn_start.configure(text=self.T("stop"))
        self.lbl_status.configure(text=self.T("running", dev=dev["name"], rate=rate, ch=ch))

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
        self.btn_start.configure(text=self.T("start"))
        self.lbl_status.configure(text=self.T("stopped"))

    def _test(self):
        self.tg = self.tg or ah.TrueGear()
        inten = int(self.prof["max_intensity"])
        self.tg.pulse(inten, inten, 120)
        allq = {"fl": inten, "fr": inten, "bl": inten, "br": inten}
        self.vest.show(allq, False)
        self.after(150, lambda: self.vest.show({}, False))

    # ------------------------------------------------------------ Hilfe
    def _show_help(self):
        if getattr(self, "_help_win", None) and self._help_win.winfo_exists():
            self._help_win.lift(); return
        win = ctk.CTkToplevel(self)
        win.title(self.T("help") + " – Audio Haptics")
        win.geometry("680x640")
        win.configure(fg_color=BG)
        self._help_win = win
        ctk.CTkLabel(win, text=self.T("help_title"), font=(FONT, 20, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(win, text=self.T("help_sub"),
                     font=(FONT, 12), text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 10))
        tabs = ctk.CTkTabview(win, fg_color=PANEL, corner_radius=14, segmented_button_fg_color=BG,
                              segmented_button_selected_color=EMBER, segmented_button_selected_hover_color=EMBER_D,
                              segmented_button_unselected_color=BG, segmented_button_unselected_hover_color=LINE,
                              text_color=TEXT)
        tabs.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        for tab, items in HELP[self.lang].items():
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

    def _test_ems(self):
        # Leichter Testreiz, fest 20: erst Band 0 (links laut Annahme), 0,8 s später Band 100
        self.tg = self.tg or ah.TrueGear()
        self.tg.ems(0, left=20, right=0); self._ems_test_l = time.time()

        def right():
            self.tg.ems(0, left=0, right=20); self._ems_test_r = time.time()
        self.after(800, right)

    # ------------------------------------------------------------ Anzeige
    def _tick(self):
        self.pb_bass.set(self.status["level"])
        self.pb_hap.set(self.status["inten"] / 100.0)
        txt = self.T("impulse") if time.time() < self.status["hit_until"] else " "
        if self.engine and time.time() - self.engine.ems_fired < 0.4:
            txt = (txt.strip() + "  ⚡ " + self.T("ems_fired")).strip()
        self.lbl_hit.configure(text=txt)
        now = time.time()
        tl = getattr(self, "_ems_test_l", 0.0); tr = getattr(self, "_ems_test_r", 0.0)
        gl = max(0.0, 1.0 - (now - tl) / 0.4); gr = max(0.0, 1.0 - (now - tr) / 0.4)
        if self.engine:
            f = max(0.0, 1.0 - (now - self.engine.ems_fired) / 0.4)
            gl = max(gl, f * self.engine.ems_lr[0]); gr = max(gr, f * self.engine.ems_lr[1])
        self.vest.show_ems(gl, gr)
        if self.engine:
            self.lbl_peak.configure(text=f"{self.T('peak')}: {self.engine.peak:.3f}")
            q, core, t = self.engine.last_q
            age = time.time() - t
            fade = max(0.0, 1.0 - age / 0.18)           # Glühen klingt in 180 ms ab
            self.vest.show(q, core, fade)
        if self.tg:
            ok = self.tg.ws is not None
            self.lbl_conn.configure(text=self.T("conn_ok") if ok else self.T("conn_lost"),
                                    text_color=EMBER if ok else MUTED)
        self.after(50, self._tick)

    def _on_close(self):
        self._stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()

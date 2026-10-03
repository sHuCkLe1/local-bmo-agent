#!/usr/bin/env python3
"""
bmo.py - a local AI agent with a BMO-style face, that you can
type to OR talk to.

  * Brain:   a local model served by Ollama (no cloud, no API keys).
  * Face:    a small window that listens, thinks, talks, etc, using the
             artwork in assets/faces/all/ (see STATE_FACES / FACE_MOODS).
  * Voice out: BMO speaks its replies with Piper (piper/ next to this file;
               pyttsx3 / Windows SAPI voices if it's missing) and the mouth
               moves with the loudness of the actual audio.
  * Voice in:  speech recognition with faster-whisper, fully offline.
  * Tools:   web search, opening websites/apps, and (with your OK) running
             commands on your computer.
  * Memory:  facts and the recent conversation are saved to bmo_memory.json,
             so BMO remembers you between runs (see /memory).
  * Wellbeing: a gentle mental-health side - a daily mood tracker, a
             gratitude log, a private journal, guided breathing and small
             coping ideas - saved to bmo_wellness.json (see /wellness). It is
             a supportive friend, not a therapist, and hands off to real
             crisis lines when something serious comes up.

Setup (Windows/macOS/Linux):
    1. Install Ollama (https://ollama.com) and:  ollama pull qwen2.5:3b
    2. python -m pip install -r requirements.txt
    3. python bmo.py

See README.md for the full guide and docs/ARCHITECTURE.md for how it works.

Voice packages are optional. Without them BMO still works as a text chat.

Talking to BMO:
    * Click the face, press SPACE in the face window, or press ENTER on an
      empty line in the terminal -> BMO listens, and stops when you go quiet.
    * Press the same thing again to finish early, ESC to shut BMO up.
    * Or just say "Hey BMO" (optionally with your question in the same
      breath: "Hey BMO, what's the weather?"). /wake turns this off.
    * /convo turns on hands-free mode: BMO keeps listening after each answer.
"""

import argparse
import collections
import html
import importlib.util
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# ----------------------------------------------------------------- palette
BODY = "#5fbfa5"      # teal case
SCREEN = "#b4ecd2"    # light green screen
SCREEN_EDGE = "#3f8f7a"
INK = "#153a30"       # captions and the little overlay doodles
HEART = "#e0457b"

KEEP_ALIVE = "30m"    # how long Ollama keeps the model in memory after use
PROMPT = "you> "

GREETINGS = [
    "Hi! I'm awake.", "Oh hey, I'm up!", "I'm here!",
    "Okay, booted up and ready.", "Hey, good to see you.",
]

STATES = ["idle", "thinking", "speaking", "happy", "error", "waking", "listening",
          "surprised", "sad", "love", "confused", "remembering", "sleeping",
          "wink", "working", "asking", "concerned", "breathing"]

# Mood tracking (see the Wellness class). A check-in is a 1-5 score with an
# optional short note; the words are here so a typed or spoken answer like
# "kind of a rough day" still lands on a number.
MOOD_WORDS = [
    (1, r"\b(awful|terrible|horrible|dreadful|miserable|rock bottom|"
        r"cant (do|take) (this|it)|can'?t (do|take) (this|it)|"
        r"the worst|despair\w*|hopeless)\b"),
    (2, r"\b(bad|rough|low|down|sad|upset|anxious|anxiety|stressed|"
        r"stress\w*|struggl\w*|hard day|down day|tough|worried|overwhelm\w*|"
        r"not (great|good|okay|ok)|meh)\b"),
    (3, r"\b(okay|ok|fine|alright|all right|so ?so|meh|neutral|"
        r"getting by|surviving)\b"),
    (4, r"\b(good|pretty good|better|decent|pleasant|calm|relaxed|"
        r"content|nice|okay actually)\b"),
    (5, r"\b(great|amazing|wonderful|fantastic|excellent|awesome|"
        r"thrilled|happy|joyful|excited|over the moon|on top of the world|"
        r"really good|so good)\b"),
]

MOOD_LABELS = {1: "rough", 2: "low", 3: "okay", 4: "good", 5: "great"}

# Kept in the user's own words, but the model should never pathologise or
# diagnose. This is a friend noticing and checking in, not a clinician.
CRISIS_PATTERNS = [
    r"\bkill myself\b", r"\bkilling myself\b", r"\bend (it|my life)\b",
    r"\bend things\b", r"\bsuicid\w*", r"\bself[- ]?harm\w*",
    r"\bcut(ting)? myself\b", r"\bhurt myself\b", r"\bdon'?t want to "
    r"(be here|live|exist)\b", r"\bwant to die\b", r"\bcan'?t go on\b",
    r"\bno reason to live\b", r"\bbetter off dead\b",
]

CRISIS_REPLY = (
    "I'm really glad you told me, and I'm here with you. But I'm just a "
    "little friend on your screen - I can't keep you safe on my own, and "
    "you deserve someone who can.\n"
    "    Please reach out to one of these, right now if you can:\n"
    "      - Call or text 988 (Suicide & Crisis Lifeline, US/Canada)\n"
    "      - Text HOME to 741741 (Crisis Text Line)\n"
    "      - Anywhere else: https://findahelpline.com\n"
    "      - In an emergency, call your local emergency number (911/112)\n"
    "    If you can, tell someone you trust and let them sit with you. "
    "You matter, and this feeling isn't forever."
)

# How BMO reacts after a reply: the first pattern that matches the user's
# words (or, failing that, BMO's reply) picks the face. Otherwise: happy.
MOODS = [
    ("love", r"\b(love you|i love|thanks?|thank you|you'?re (the best|great|"
             r"awesome|amazing|cute|sweet|funny)|good (job|bot)|cute|adorable)\b"),
    ("sad", r"\b(sad|sorry|miss(ed)? you|lonely|died|passed away|depressed|"
            r"cry(ing)?|bad day|upset|unfortunately)\b"),
    ("surprised", r"\b(wow|whoa|woah|no way|guess what|oh my|incredible|"
                  r"unbelievable|seriously)\b|\breally\?"),
    ("confused", r"\b(i don'?t (know|understand)|what do you mean|confus\w*|"
                 r"huh|not sure)\b"),
]


# Expression faces in assets/faces/all/, grouped by what the art shows (the
# filenames don't always match the picture, e.g. "happy_with_heart" has no
# heart but "playful_tongue" does).
FACE_MOODS = {
    "joy": ["cheerful_grin", "laughing", "joyful_laugh", "playful_happy",
            "happy_smile", "deadpan", "joyful_open_smile"],
    "love": ["sleepy_kiss", "blowing_kiss", "puckered_kiss", "playful_tongue"],
    "playful": ["happy_open_smile", "playful_wink", "kissing", "cool_smirk",
                "excited_laugh", "mischievous_grin", "cheeky_tongue",
                "big_laugh", "blank_expression", "happy_with_heart"],
    "surprised": ["worried_surprise", "neutral", "surprised", "shocked",
                  "blank_mouth", "deadpan_shock", "surprised_open_mouth",
                  "astonished", "wide_eyed_surprise", "expressionless",
                  "tongue_out", "displeased"],
    "sad": ["distressed", "crying", "downcast", "loved_up", "worried",
            "grumpy_sad"],
    "angry": ["unimpressed", "sad", "angry", "disgusted", "nervous",
              "angry_frown", "annoyed", "grumpy"],
    "confused": ["confused_skeptic", "dizzy", "knocked_out", "sleepy"],
    "sleepy": ["tired_yawn", "peaceful", "weary"],
    "shy": ["blushing_smile", "blushing_happy", "cute_shy", "disappointed"],
    "calm": ["blank_stare", "content_squint", "bored", "content_smile",
             "ecstatic"],
}

# Per-sentence feelings, checked in order; the first match wins. Builds on
# MOODS (used for the reaction face after a reply). No match -> "calm": a
# neutral face between strong moments reads better than constant cheer.
SENTENCE_MOODS = [
    ("sad", dict(MOODS)["sad"] + r"|\b(sorry to hear|tough|hard day|hurts?|"
                                 r"wish i could|lost|alone|tears?)\b"),
    ("angry", r"\b(angry|mad|annoy\w*|grr+|ugh+|hate|furious|grumpy|"
              r"rude|unfair|irritat\w*|frustrat\w*)\b"),
    ("love", dict(MOODS)["love"] + r"|\b(hugs?|my friend|best friend|"
                                   r"glad you'?re here|care about)\b"),
    ("shy", r"\b(blush\w*|embarrass\w*|shy|aw+ shucks|you'?re making me|"
            r"stop it|oh stop|flattered)\b"),
    ("sleepy", r"\b(sleepy|tired|yawn\w*|nap|bed ?time|sleep\w*|zzz+|"
               r"exhausted|good ?night)\b"),
    ("confused", dict(MOODS)["confused"] + r"|\b(hmm+|weird|strange|"
                                           r"puzzl\w*|which one|wait,)\b"),
    ("surprised", dict(MOODS)["surprised"] + r"|\b(can'?t believe|"
                                             r"what\?!|oh!)\b"),
    ("playful", r"\b(ha(ha)+|hehe+|lol|kidding|joke|jk|tease|teasing|"
                r"silly|sneaky|gotcha|bet you|just saying|wink)\b"),
    ("joy", r"\b(yay+|woo+(hoo)?|hooray|awesome|amazing|exciting|excited|"
            r"so cool|fun|great|love it|best|fantastic|wonderful|"
            r"happy|glad)\b"),
]


def classify_sentence(text):
    """Which FACE_MOODS group fits one sentence of BMO's reply."""
    for mood, pat in SENTENCE_MOODS:
        if re.search(pat, text, re.I):
            return mood
    return "joy" if text.rstrip().endswith("!") else "calm"


def pick_mood(user_text, reply):
    for text in (user_text, reply):
        for mood, pat in MOODS:
            if re.search(pat, text, re.I):
                return mood
    return "happy"


# Every persona state is shown with one of the provided faces in
# assets/faces/all/ (keys are the filenames without the "NN_" prefix). There
# is no drawn/animated fallback face any more: these are the only faces BMO
# ever shows. "speaking" is handled separately - it cycles through FACE_MOODS
# sentence by sentence - so it is not listed here.
STATE_FACES = {
    "idle": "content_smile",
    "thinking": "content_squint",
    "waking": "tired_yawn",
    "listening": "neutral",
    "happy": "cheerful_grin",
    "error": "downcast",
    "surprised": "shocked",
    "sad": "sad",
    "love": "loved_up",
    "confused": "confused_skeptic",
    "remembering": "blank_stare",
    "sleeping": "peaceful",
    "wink": "playful_wink",
    "working": "blank_expression",
    "asking": "worried_surprise",
    "concerned": "worried",
    "breathing": "peaceful",
}

# Idle slowly drifts between these calm faces so BMO still feels alive, while
# never leaving the provided art.
IDLE_FACES = ("content_smile", "neutral", "content_squint", "happy_smile")


def reprompt():
    """Re-show the input prompt after a background thread printed something."""
    print(PROMPT, end="", flush=True)


# -------------------------------------------------------------------- face
class Face:
    """Draws the face on a Tk canvas and smoothly animates between states."""

    FIDGET_SECS = {"glance": 1.6, "wink": 0.5, "yawn": 2.2}
    SPRITE_SIZE = (260, 190)       # pre-scaled to sit inside the screen

    def __init__(self, root, size=(440, 400), sleep_after=300):
        self.root = root
        self.canvas = tk.Canvas(
            root, width=size[0], height=size[1], bg=BODY, highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.t0 = time.time()
        self.state = "idle"
        self.caption = None
        self.idle_caption = "idle"     # e.g. 'say "hey BMO"' when wake word is on
        self.revert_at = None      # auto-return to idle at this time
        self.talk_until = 0.0      # mouth flaps until this time
        self.envelope = None       # (loudness list 0..1, start time, window secs)
        self.quit_requested = False
        self.mic_level = 0.0       # 0..1, written by the listener
        self.mic_smooth = 0.0

        # idle life: little fidgets, and dozing off when left alone
        self.sleep_after = sleep_after     # seconds; 0 = never sleep
        self.last_active = self.t0
        self.fidget, self.fidget_until = None, 0.0
        self.next_fidget = self.t0 + random.uniform(8, 16)
        self.glance = 0.0

        # how far the shown face bobs up while speaking (eases to the audio)
        self.mouth_open = 0.0

        # The provided faces in assets/faces/all/ are the ONLY faces BMO
        # shows. STATE_FACES maps each persona state onto one of them, and
        # FACE_MOODS picks them sentence by sentence while speaking.
        faces = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "faces")
        all_dir = os.path.join(faces, "all")
        try:
            names = sorted(f for f in os.listdir(all_dir) if f.endswith(".png"))
        except OSError:
            names = []
        self.expr_sprites = self.load_sprites(      # {"cheerful_grin": image}
            {re.sub(r"^\d+_", "", f[:-4]): os.path.join(all_dir, f)
             for f in names})
        self.expression = STATE_FACES["idle"]   # shown until the first sentence
        self.idle_face = STATE_FACES["idle"]    # drifts between calm faces
        self.next_idle_face = 0.0
        self.expr_queue = collections.deque()   # muted: (mood, hold secs)
        self.expr_until = 0.0

        self.tick()

    def load_sprites(self, paths):
        """{key: png path} -> {key: PhotoImage} for the files that exist. The
        art's own card (background and border) is cut away so just the face
        sits on BMO's screen. Needs Pillow; without it (or with no face art)
        BMO has nothing to show, so make sure pillow is installed."""
        try:
            from PIL import Image, ImageChops, ImageTk
        except ImportError:
            return {}
        sprites = {}
        for key, path in paths.items():
            if not os.path.isfile(path):
                continue
            try:
                im = Image.open(path).convert("RGB")
                w, h = im.size
                m = int(min(w, h) * 0.07)        # outer margin + card border
                im = im.crop((m, m, w - m, h - m))
                # shrink first: keying a small image is much faster
                im.thumbnail(self.SPRITE_SIZE, Image.LANCZOS)
                # card colour -> transparent; anything darker (ink, mouth,
                # hearts) stays, with soft edges where it blends into the card
                patch = im.crop((0, im.height // 2 - 4, 6, im.height // 2 + 4))
                card = Image.new("RGB", im.size, patch.resize((1, 1)).getpixel((0, 0)))
                im.putalpha(ImageChops.difference(im, card).convert("L")
                            .point(lambda v: 0 if v < 8 else min(255, v * 4)))
                sprites[key] = ImageTk.PhotoImage(im)
            except Exception as e:
                print(f"\n[!] couldn't load face {path}: {e}")
        return sprites

    # ---- controls (plain attribute writes, safe from other threads) ----
    def set_state(self, state, hold=None, caption=None):
        self.state = state
        self.caption = caption     # overrides the text under the screen
        self.revert_at = (time.time() + hold) if hold else None
        self.last_active = time.time()

    def talk(self, n_chars):
        """Call whenever a word is 'spoken'; keeps the mouth moving briefly."""
        self.talk_until = time.time() + min(0.35, 0.12 + 0.05 * n_chars)
        self.last_active = time.time()

    def set_envelope(self, envelope, start_time=None, window_secs=0.03):
        """Drive the speaking mouth from the audio's loudness: envelope[i] is
        the 0..1 level of the window starting at start_time + i * window_secs.
        None clears it (back to word-flapping)."""
        self.envelope = (None if envelope is None else
                         (envelope, start_time or time.time(), window_secs))
        self.last_active = time.time()

    def show_expression(self, mood):
        """Switch the speaking face to a random one from a FACE_MOODS group
        (a different one from the current face when there's a choice)."""
        names = [n for n in FACE_MOODS.get(mood, ()) if n in self.expr_sprites]
        if names:
            self.expression = random.choice(
                [n for n in names if n != self.expression] or names)

    def queue_expression(self, mood, hold=1.5):
        """Muted replies: there's no audio to sync to, so show each sentence's
        face for at least `hold` seconds, one after another."""
        self.expr_queue.append((mood, hold))

    def expressions_pending(self):
        return bool(self.expr_queue) or time.time() < self.expr_until

    def face_key(self):
        """Which provided face to show for the current state. Speaking uses
        the sentence's expression; idle drifts between calm faces; every
        other state maps through STATE_FACES. Falls back to any loaded face."""
        if self.state == "speaking" and self.expression in self.expr_sprites:
            return self.expression
        key = None
        if self.state == "idle":
            if self.fidget == "wink":
                key = "playful_wink"
            elif self.fidget == "yawn":
                key = "tired_yawn"
            else:
                key = self.idle_face
        if key is None:
            key = STATE_FACES.get(self.state)
        if key not in self.expr_sprites:
            key = next(iter(self.expr_sprites), None)
        return key

    def quit(self):
        self.quit_requested = True

    # ---- animation loop ----
    def tick(self):
        if self.quit_requested:
            self.root.destroy()
            return

        now = time.time()
        t = now - self.t0

        if self.revert_at and now >= self.revert_at:
            self.state, self.revert_at, self.caption = "idle", None, None
        if self.state == "idle":
            self.idle_life(now)
        else:
            self.fidget = None
        if self.state != "speaking":
            self.expr_queue.clear()
            self.expr_until = 0.0
        elif self.expr_queue and now >= self.expr_until:
            mood, hold = self.expr_queue.popleft()
            self.show_expression(mood)
            self.expr_until = now + hold

        self.mic_smooth += (self.mic_level - self.mic_smooth) * 0.3

        # The faces are the provided art, so there is no drawn eye/mouth to
        # animate any more. Only the speaking lift follows the voice: how far
        # the face bobs up with the loudness of the audio (or the word flap).
        mouth = 0.0
        if self.state == "speaking":
            if self.envelope:
                env, start, win = self.envelope
                i = int((now - start) / win)
                if 0 <= i < len(env):
                    mouth = 0.85 * env[i]
            elif now < self.talk_until:
                mouth = 0.25 + 0.6 * abs(math.sin(t * 16 + random.random()))
        self.mouth_open += (mouth - self.mouth_open) * 0.28

        self.draw(t)
        self.root.after(16, self.tick)

    def idle_life(self, now):
        """Little things BMO does on its own while nobody's talking to it:
        a fidget (a different face for a moment) now and then, and a slow
        drift between calm faces so idle never looks frozen."""
        if self.fidget and now >= self.fidget_until:
            self.fidget = None
        if self.sleep_after and now - self.last_active > self.sleep_after:
            self.state, self.fidget = "sleeping", None
        elif self.fidget is None and now >= self.next_fidget:
            self.fidget = random.choice(["glance", "glance", "wink", "yawn"])
            self.fidget_until = now + self.FIDGET_SECS[self.fidget]
            self.glance = random.choice((-1, 1)) * random.uniform(0.6, 1.0)
            self.next_fidget = now + random.uniform(8, 20)
        # drift between calm faces so idle never looks frozen
        if self.fidget is None and now >= self.next_idle_face:
            names = [n for n in IDLE_FACES if n in self.expr_sprites]
            if names:
                self.idle_face = random.choice(
                    [n for n in names if n != self.idle_face] or names)
            self.next_idle_face = now + random.uniform(6, 14)

    # ---- drawing ----
    def rounded_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def draw(self, t):
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 100), max(c.winfo_height(), 100)

        # screen
        sx1, sx2 = w * 0.07, w * 0.93
        sy1, sy2 = h * 0.06, h * 0.86
        sw, sh = sx2 - sx1, sy2 - sy1
        self.rounded_rect(sx1, sy1, sx2, sy2, sw * 0.06,
                          fill=SCREEN, outline=SCREEN_EDGE, width=4)

        bob = math.sin(t * 1.6) * 2 if self.state == "idle" else 0
        cx = (sx1 + sx2) / 2

        st = self.state
        # the provided face for this state (speaking: this sentence's mood)
        key = self.face_key()
        if key is not None:
            # while speaking the face is nudged up with the voice's loudness;
            # idle keeps a gentle bob so BMO still feels alive
            lift = (self.mouth_open * sh * 0.035 if st == "speaking"
                    else abs(bob))
            c.create_image(cx, (sy1 + sy2) / 2 - lift,
                           image=self.expr_sprites[key])

        # thinking dots
        if self.state == "thinking":
            for i in range(3):
                dy = -abs(math.sin(t * 4 - i * 0.8)) * 10
                dx = sx2 - sw * 0.16 + i * sw * 0.045
                c.create_oval(dx - 4, sy1 + sh * 0.12 + dy - 4,
                              dx + 4, sy1 + sh * 0.12 + dy + 4,
                              fill=INK, outline=INK)

        # sleepy Z's drifting up while waking / sleeping
        if st in ("waking", "sleeping"):
            for i in range(3):
                p = ((t * 0.5) + i / 3) % 1.0            # 0..1 life of each Z
                zx = sx2 - sw * 0.22 + p * sw * 0.08
                zy = sy1 + sh * 0.30 - p * sh * 0.22
                c.create_text(zx, zy, text="z", fill=INK,
                              font=("Helvetica", int(11 + 12 * p), "bold"))

        # little equalizer bars that dance with your voice while listening
        if self.state == "listening":
            for i in range(5):
                wob = 0.5 + 0.5 * abs(math.sin(t * 9 + i * 1.3))
                bh = 4 + (0.2 + 0.8 * self.mic_smooth) * 28 * wob
                bx = sx2 - sw * 0.24 + i * sw * 0.045
                by = sy1 + sh * 0.14
                c.create_rectangle(bx - 3, by - bh / 2, bx + 3, by + bh / 2,
                                   fill=INK, outline="")

        # corner decorations for the reaction faces
        corner_x, corner_y = sx2 - sw * 0.14, sy1 + sh * 0.16
        if st == "surprised":
            c.create_text(corner_x, corner_y + math.sin(t * 10) * 2, text="!",
                          fill=INK, font=("Helvetica", 26, "bold"))
        elif st == "confused":
            for i in range(2):                     # question marks floating up
                p = ((t * 0.4) + i / 2) % 1.0
                c.create_text(corner_x + i * sw * 0.05 + p * sw * 0.02,
                              corner_y + sh * 0.12 - p * sh * 0.18, text="?",
                              fill=INK, font=("Helvetica", int(14 + 10 * p), "bold"))
        elif st == "love":
            for i in range(2):                     # little hearts floating up
                p = ((t * 0.35) + i / 2) % 1.0
                self.draw_heart(corner_x + i * sw * 0.06,
                                corner_y + sh * 0.12 - p * sh * 0.2,
                                5 + 6 * math.sin(math.pi * p))
        elif st == "remembering":
            s = 10 + 5 * abs(math.sin(t * 5))      # twinkling sparkle
            q = s * 0.3
            c.create_polygon(corner_x, corner_y - s, corner_x + q, corner_y - q,
                             corner_x + s, corner_y, corner_x + q, corner_y + q,
                             corner_x, corner_y + s, corner_x - q, corner_y + q,
                             corner_x - s, corner_y, corner_x - q, corner_y - q,
                             fill=INK, outline="")
        elif st == "working":
            for i in range(8):                     # spinner
                a = t * 5 + i * math.pi / 4
                r = 2 + 3 * (i / 7)
                dx, dy = corner_x + math.cos(a) * 14, corner_y + math.sin(a) * 14
                c.create_oval(dx - r, dy - r, dx + r, dy + r, fill=INK, outline="")
        elif st == "asking":
            c.create_text(corner_x, corner_y, text="?", fill=INK,
                          font=("Helvetica", 26, "bold"))

        # caption under the screen
        caption = self.caption or {
            "idle": self.idle_caption,
            "waking": "waking up...", "listening": "listening...",
            "sleeping": "zzz...", "remembering": "remembering...",
            "wink": "happy", "working": "working...",
            "asking": "yes or no?", "concerned": "i'm here",
            "breathing": "breathe with me..."}.get(st, st)
        c.create_text(w / 2, h * 0.93, text=caption, fill=INK,
                      font=("Helvetica", 11, "bold"))

    def draw_heart(self, x, y, size):
        pts = []
        for i in range(24):
            u = 2 * math.pi * i / 24
            hx = 16 * math.sin(u) ** 3
            hy = (13 * math.cos(u) - 5 * math.cos(2 * u)
                  - 2 * math.cos(3 * u) - math.cos(4 * u))
            pts += [x + hx * size / 16, y - hy * size / 16]
        self.canvas.create_polygon(pts, fill=HEART, outline=HEART, smooth=True)


# ----------------------------------------------------------------- speaker
PIPER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "piper")
PIPER_EXE = os.path.join(PIPER_DIR, "piper.exe" if os.name == "nt" else "piper")


def piper_voices():
    """Piper voice models in piper/ (each .onnx needs its .onnx.json)."""
    try:
        names = sorted(os.listdir(PIPER_DIR))
    except OSError:
        return []
    return [os.path.join(PIPER_DIR, n) for n in names
            if n.endswith(".onnx") and n + ".json" in names]


def piper_missing():
    """Why Piper can't be used, with how to fix it - or None if it's ready."""
    missing = []
    if not os.path.isfile(PIPER_EXE):
        missing.append(f"piper binary ({PIPER_EXE})")
    if not piper_voices():
        missing.append(f"a voice model (.onnx + .onnx.json) in {PIPER_DIR}")
    if not missing:
        return None
    return ("Piper not found - missing " + " and ".join(missing) + ".\n"
            "  To use Piper:\n"
            "   1. Download piper_windows_amd64.zip from\n"
            "      https://github.com/rhasspy/piper/releases\n"
            f"   2. Unzip it so piper.exe ends up at {PIPER_EXE}\n"
            "   3. Put a voice's .onnx and .onnx.json in that folder\n"
            "      (voices: https://huggingface.co/rhasspy/piper-voices)")


def piper_synth(text, voice, length_scale=1.0):
    """Run piper on one sentence; returns (int16 mono samples, sample rate)."""
    import numpy as np
    with open(voice + ".json", encoding="utf-8") as f:
        rate = json.load(f)["audio"]["sample_rate"]
    p = subprocess.run(
        [PIPER_EXE, "--model", voice, "--output_raw", "--quiet",
         "--length_scale", f"{length_scale:.3f}"],
        input=text.encode("utf-8"), capture_output=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        err = p.stderr.decode(errors="replace").strip()
        raise RuntimeError(err[-300:] or f"piper exited with {p.returncode}")
    return np.frombuffer(p.stdout, dtype=np.int16), rate


def loudness_envelope(samples, rate, window_secs):
    """RMS loudness per window, scaled to 0..1. Near-silent windows become 0
    so the mouth closes in the pauses between words."""
    import numpy as np
    n = max(1, int(rate * window_secs))
    x = samples.astype(np.float32)
    x = np.pad(x, (0, -len(x) % n))
    rms = np.sqrt((x.reshape(-1, n) ** 2).mean(axis=1))
    # scale by a high percentile, not the max, so one loud pop doesn't make
    # the rest of the sentence look quiet
    peak = float(np.percentile(rms, 95)) if len(rms) else 0.0
    if peak <= 0:
        return [0.0] * len(rms)
    env = np.clip(rms / peak, 0.0, 1.0)
    env[env < 0.1] = 0.0
    return env.tolist()


class Speaker:
    """Text-to-speech on background threads. Uses Piper (piper/ next to
    bmo.py) and drives the mouth from the loudness of the actual audio.

    If Piper isn't installed it falls back to pyttsx3 (Windows SAPI voices),
    where the mouth flaps on the engine's word events instead. Text is queued
    sentence by sentence."""

    WINDOW = 0.03                   # seconds of audio per mouth-envelope step

    def __init__(self, face, rate=190, voice_hint=None, disabled=False):
        self.face = face
        self.rate = rate
        self.voice_hint = voice_hint
        self.enabled = True             # /mute toggles this
        self.ok = False                 # engine came up successfully
        self.err = None
        self.voice_names = []
        self.engine = None
        self.ready = threading.Event()
        self.idle = threading.Event()   # set when nothing is queued or playing
        self.idle.set()
        self.q = queue.Queue()          # sentences waiting to be spoken
        self._audio_q = queue.Queue()   # piper: synthesized, waiting to play
        self._lock = threading.Lock()
        self._pending = 0
        self._interrupt = threading.Event()
        self._new_voice = None
        self._voice_id = None
        self._voice_label = None
        self._voice_path = None
        if disabled:
            self.err = "disabled"
            self.ready.set()
        elif piper_missing() is None:
            threading.Thread(target=self._piper_worker, daemon=True).start()
        else:
            threading.Thread(target=self._sapi_worker, daemon=True).start()

    @property
    def active(self):
        return self.enabled and self.ok

    # ---- public API ----
    def say(self, text, mood=None):
        """Queue a sentence; mood is a FACE_MOODS group for the face to show
        while it's heard (worked out from the text if not given)."""
        with self._lock:
            self._pending += 1
            self.idle.clear()
        self.q.put((text, mood or classify_sentence(text)))

    def wait_done(self, timeout=None):
        return self.idle.wait(timeout)

    def stop(self):
        """Drop everything queued and cut off the current sentence."""
        self._interrupt.set()
        for q in (self.q, self._audio_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
                self._done_one()
        self.idle.wait(timeout=3)
        self._interrupt.clear()

    def set_voice(self, hint):
        self._new_voice = hint

    # ---- internals ----
    def _done_one(self):
        with self._lock:
            self._pending = max(0, self._pending - 1)
            if self._pending == 0:
                self.idle.set()

    # ---- piper ----
    def _piper_find_voice(self, hint, quiet=False):
        """Pick a piper/ voice model by (partial, case-insensitive) name."""
        for path in piper_voices():
            name = os.path.basename(path)[:-len(".onnx")]
            if hint.lower() in name.lower():
                self._voice_path, self._voice_label = path, name
                if not quiet:
                    print(f"\n[voice: {name}]")
                    reprompt()
                return True
        if not quiet:
            print(f"\n[!] No Piper voice matching '{hint}'. Try /voices")
            reprompt()
        return False

    def _piper_worker(self):
        """Turns queued sentences into audio. Runs one sentence ahead of
        _piper_player, so the next sentence is ready when this one ends."""
        voices = piper_voices()
        self.voice_names = [os.path.basename(v)[:-len(".onnx")] for v in voices]
        self._voice_path, self._voice_label = voices[0], self.voice_names[0]
        if self.voice_hint:
            self._piper_find_voice(self.voice_hint, quiet=True)
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            self.ok = True
        except Exception as e:
            self.err = e
        self.ready.set()
        if not self.ok:
            print("\n[voice output off - run: pip install numpy sounddevice]"
                  f"   ({self.err})")
            reprompt()
            return
        print(f"\n[voice engine ready - piper, voice={self._voice_label!r} "
              f"rate={self.rate}]")
        reprompt()
        threading.Thread(target=self._piper_player, daemon=True).start()

        while True:
            text, mood = self.q.get()
            if self._interrupt.is_set():
                self._done_one()
                continue
            try:
                if self._new_voice:
                    self._piper_find_voice(self._new_voice)
                    self._new_voice = None
                # piper's speed knob is phoneme length: >1 slower, <1 faster
                samples, rate = piper_synth(text, self._voice_path,
                                            190 / self.rate)
            except Exception as e:
                print(f"\n[!] voice error: {e}")
                reprompt()
                self._done_one()
                continue
            if self._interrupt.is_set():
                self._done_one()
            else:
                self._audio_q.put((samples, rate, mood))

    def _piper_player(self):
        import sounddevice as sd
        while True:
            samples, rate, mood = self._audio_q.get()
            try:
                if self._interrupt.is_set():
                    continue
                env = loudness_envelope(samples, rate, self.WINDOW)
                self.face.set_state("speaking")
                sd.play(samples, rate)
                # change face as it's heard, not when queued; after play()
                # because opening the device can take ~1s the first time
                self.face.show_expression(mood)
                try:    # output latency: when the first sample is actually heard
                    lag = float(sd.get_stream().latency)
                except Exception:
                    lag = 0.0
                start = time.time() + lag
                self.face.set_envelope(env, start, self.WINDOW)
                end = start + len(samples) / rate
                while time.time() < end:
                    if self._interrupt.is_set():
                        sd.stop()
                        break
                    time.sleep(0.02)
            except Exception as e:
                print(f"\n[!] voice error: {e}")
                reprompt()
            finally:
                self.face.set_envelope(None)
                self._done_one()

    # ---- pyttsx3 fallback (when piper/ isn't set up) ----
    def _sapi_find_voice(self, engine, hint, quiet=False):
        """Look up a voice id by (partial, case-insensitive) name. Stores the
        match on self._voice_id/self._voice_label for reuse by later engines."""
        for v in engine.getProperty("voices") or []:
            if hint.lower() in v.name.lower():
                self._voice_id, self._voice_label = v.id, v.name
                if not quiet:
                    print(f"\n[voice: {v.name}]")
                    reprompt()
                return True
        if not quiet:
            print(f"\n[!] No voice matching '{hint}'. Try /voices")
            reprompt()
        return False

    def _sapi_on_word(self, name, location, length):
        self.face.talk(length)
        if self._interrupt.is_set() and self.engine is not None:
            self.engine.stop()

    def _sapi_engine(self):
        """A pyttsx3/SAPI engine object reliably speaks only ONCE on Windows -
        the second runAndWait() on a reused engine often reports success but
        stays silent. So we build a fresh, cheap engine for every sentence
        instead of reusing one across calls."""
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        engine.setProperty("volume", 1.0)
        if self._voice_id:
            engine.setProperty("voice", self._voice_id)
        engine.connect("started-word", self._sapi_on_word)
        return engine

    def _sapi_worker(self):
        print(f"\n[{piper_missing()}\n  Using the basic pyttsx3 voice for now.]")
        reprompt()
        try:
            try:  # Windows: this thread needs COM initialised for SAPI
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass
            import pyttsx3
            probe = pyttsx3.init()
            self.voice_names = [v.name for v in (probe.getProperty("voices") or [])]
            self._sapi_find_voice(probe, self.voice_hint or "zira",
                                  quiet=not self.voice_hint)
            if not self._voice_id and probe.getProperty("voices"):
                v0 = probe.getProperty("voices")[0]
                self._voice_id, self._voice_label = v0.id, v0.name
            print(f"\n[voice engine ready - pyttsx3, voice={self._voice_label!r} "
                  f"rate={self.rate}]")
            reprompt()
            del probe
            self.ok = True
        except Exception as e:
            self.err = e
        self.ready.set()

        if not self.ok:
            print("\n[voice output off - run: pip install pyttsx3]"
                  f"   ({self.err})")
            reprompt()
            return

        while True:
            text, mood = self.q.get()
            try:
                if self._interrupt.is_set():
                    continue
                if self._new_voice:
                    self._sapi_find_voice(self._sapi_engine(), self._new_voice)
                    self._new_voice = None
                self.face.set_state("speaking")
                self.face.show_expression(mood)
                engine = self._sapi_engine()
                self.engine = engine
                engine.say(text)
                engine.runAndWait()
                self.engine = None
                del engine
            except Exception as e:
                import traceback
                print(f"\n[!] voice error: {e}\n{traceback.format_exc()}")
                reprompt()
            finally:
                self._done_one()


# ---------------------------------------------------------------- listener
class Listener:
    """Microphone + offline speech recognition (faster-whisper)."""

    RATE = 16000

    def __init__(self, face, model_name="base.en", device=None, hotwords=None):
        self.face = face
        self.model_name = model_name
        self.device = device
        self.hotwords = hotwords        # e.g. "BMO Beemo": helps whisper spell names
        self.model = None
        self.ok = False
        self.err = None
        self.recording = False
        self.stop_flag = threading.Event()
        self.loaded = threading.Event()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        missing = [m for m in ("faster_whisper", "sounddevice", "numpy")
                   if importlib.util.find_spec(m) is None]
        if missing:
            self.err = "missing packages: " + ", ".join(missing)
            print("\n[voice input off - run: pip install faster-whisper sounddevice]")
            self.loaded.set()
            reprompt()
            return
        try:
            print("\n[loading speech recognition - the first run downloads the model]")
            import numpy as np
            import sounddevice as sd
            from faster_whisper import WhisperModel
            self.np, self.sd = np, sd
            self.model = WhisperModel(self.model_name, device="cpu",
                                      compute_type="int8")
            self.ok = True
            print("[ears ready - click the face, press SPACE in its window, "
                  "or press ENTER on an empty line to talk]")
        except Exception as e:
            self.err = e
            print(f"\n[!] Couldn't load speech recognition: {e}")
        self.loaded.set()
        reprompt()

    def record(self, max_secs=20.0, silence_secs=1.1, wait_secs=7.0):
        """Record until you stop talking. Returns float32 audio, or None."""
        np, sd = self.np, self.sd
        chunks = queue.Queue()
        block = int(self.RATE * 0.03)   # 30 ms blocks

        def callback(indata, frames, t_info, status):
            chunks.put(indata[:, 0].copy())

        audio, calib = [], []
        started, quiet_for, noise = False, 0.0, None
        t0 = time.time()
        self.stop_flag.clear()
        self.recording = True
        try:
            with sd.InputStream(samplerate=self.RATE, channels=1, dtype="float32",
                                blocksize=block, callback=callback,
                                device=self.device):
                while True:
                    elapsed = time.time() - t0
                    if self.stop_flag.is_set() or elapsed > max_secs:
                        break
                    if not started and elapsed > wait_secs:
                        return None
                    try:
                        chunk = chunks.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    self.face.mic_level = min(1.0, rms * 10)
                    audio.append(chunk)

                    if elapsed < 0.3:               # measure the room's noise floor
                        calib.append(rms)
                        continue
                    if noise is None:
                        noise = min(0.02, float(np.mean(calib)) if calib else 0.005)
                    threshold = max(0.015, noise * 3.0)

                    if rms > threshold:
                        started, quiet_for = True, 0.0
                    elif started:
                        quiet_for += 0.03
                        if quiet_for >= silence_secs:
                            break
        finally:
            self.recording = False
            self.face.mic_level = 0.0

        if not audio:
            return None
        data = np.concatenate(audio)
        # if stopped by hand, keep whatever we got (if it's long enough)
        if not started and len(data) < self.RATE * 0.4:
            return None
        return data

    def transcribe(self, audio):
        lang = "en" if self.model_name.endswith(".en") else None
        segments, _ = self.model.transcribe(
            audio, language=lang, beam_size=1, vad_filter=True,
            condition_on_previous_text=False, hotwords=self.hotwords)
        text = " ".join(s.text.strip() for s in segments).strip()
        # whisper sometimes hallucinates these on near-silence
        if text.lower().strip(" .!?") in ("", "you", "thanks for watching",
                                          "thank you for watching"):
            return ""
        return text


# --------------------------------------------------------------- wake word
class Segmenter:
    """Cuts a live mic stream (30 ms blocks) into short utterances by
    loudness. Long speech is cut at max_secs and the rest skipped until the
    next pause, so the wake word detector only ever transcribes short clips."""

    BLOCK = 0.03

    def __init__(self, max_secs=3.0, end_silence=0.6, preroll=0.3,
                 min_speech=0.2):
        self.max_secs, self.end_silence = max_secs, end_silence
        self.min_speech = min_speech
        self.pre = collections.deque(maxlen=int(preroll / self.BLOCK))
        self.noise = 0.01           # running estimate of the room's loudness
        self.state = "quiet"        # quiet -> speech -> quiet (or skip)
        self.seg, self.quiet, self.loud = [], 0.0, 0.0

    def feed(self, chunk, rms):
        """Returns a list of chunks when an utterance ends, else None."""
        loud = rms > max(0.015, self.noise * 3.0)
        if self.state == "quiet":
            if loud:
                self.state = "speech"
                self.seg, self.quiet, self.loud = list(self.pre) + [chunk], 0.0, 0.0
            else:
                self.noise = min(0.02, 0.95 * self.noise + 0.05 * rms)
                self.pre.append(chunk)
            return None
        if self.state == "skip":    # rest of a long utterance: wait for a pause
            self.quiet = 0.0 if loud else self.quiet + self.BLOCK
            if self.quiet >= self.end_silence:
                self.state = "quiet"
                self.pre.clear()
            return None

        self.seg.append(chunk)
        if loud:
            self.quiet, self.loud = 0.0, self.loud + self.BLOCK
        else:
            self.quiet += self.BLOCK
        if (self.quiet >= self.end_silence
                or len(self.seg) * self.BLOCK >= self.max_secs):
            done, enough = self.seg, self.loud >= self.min_speech
            self.state = "quiet" if self.quiet >= self.end_silence else "skip"
            self.seg, self.quiet = [], 0.0
            self.pre.clear()
            return done if enough else None
        return None


class WakeWord:
    """Always-on 'Hey BMO' detector. Listens for short bursts of speech,
    transcribes them with a small whisper model, and wakes BMO when the
    name comes first. Pauses itself whenever BMO is busy, listening or
    talking, so it never hears BMO's own voice."""

    # how whisper tends to write "BMO" / "Beemo". The strong spellings count
    # anywhere near the start; the weak ones only right after hey/hi/ok.
    STRONG = [r"b\W?m\W?o", r"bee?mo", r"beamo"]
    WEAK = [r"bima", r"beema", r"bml", r"be\W?e?mo", r"be\W?ammo", r"beame?r",
            r"beemer", r"fema", r"vmo", r"pmo"]
    GREET = r"(?:hey|hae|hay|hi|hello|okay|ok|yo|oh)"

    def __init__(self, agent, listener, model_name="tiny.en", debug=False):
        self.agent, self.listener = agent, listener
        self.model_name = model_name
        self.debug = debug              # print everything it hears
        self.enabled = True
        self.ready = False
        self.model = None
        self.cooldown_until = 0.0
        self._last_err = None
        name = re.escape(agent.name.lower())
        strong = "|".join([name] + self.STRONG)
        weak = "|".join(self.WEAK)
        # (optional filler word) name, then the rest of what was said
        self.pattern = re.compile(
            rf"^\W*(?:{self.GREET}\W+(?:{strong}|{weak})"
            rf"|(?:\w+\W+)?(?:{strong}))\b\W*(.*)$", re.I | re.S)
        self.hotwords = f"{agent.name} Beemo"
        threading.Thread(target=self._run, daemon=True).start()

    def match(self, text):
        """Returns what came after the wake word ('' if nothing), or None."""
        m = self.pattern.match(text.strip())
        return m.group(1).strip() if m else None

    def _paused(self):
        a = self.agent
        return (not self.enabled or a.busy or a.turn_lock.locked()
                or a.awaiting_answer or self.listener.recording
                or not a.speaker.idle.is_set()
                or time.time() < self.cooldown_until)

    def _run(self):
        L = self.listener
        L.loaded.wait()
        if not L.ok:
            return
        try:
            if self.model_name == L.model_name:
                self.model = L.model
            else:
                from faster_whisper import WhisperModel
                self.model = WhisperModel(self.model_name, device="cpu",
                                          compute_type="int8")
        except Exception as e:
            print(f"\n[!] Wake word off - couldn't load '{self.model_name}': {e}")
            return reprompt()
        self.ready = True
        print(f"\n[wake word ready - say \"Hey {self.agent.name}\" "
              "(/wake turns it off)]")
        reprompt()
        while True:
            if self._paused():
                time.sleep(0.2)
                continue
            try:
                self._listen()
            except Exception as e:
                if str(e) != self._last_err:    # don't repeat the same error
                    self._last_err = str(e)
                    print(f"\n[!] Wake word mic problem: {e}")
                    reprompt()
                time.sleep(3)

    def _listen(self):
        """Keep the mic open until paused or the wake word is heard."""
        np, sd, L = self.listener.np, self.listener.sd, self.listener
        chunks = queue.Queue()
        seg = Segmenter()

        def callback(indata, frames, t_info, status):
            chunks.put(indata[:, 0].copy())

        with sd.InputStream(samplerate=L.RATE, channels=1, dtype="float32",
                            blocksize=int(L.RATE * Segmenter.BLOCK),
                            callback=callback, device=L.device):
            while not self._paused():
                try:
                    chunk = chunks.get(timeout=0.2)
                except queue.Empty:
                    continue
                parts = seg.feed(chunk, float(np.sqrt(np.mean(chunk ** 2))))
                if parts and self._check(np.concatenate(parts)):
                    return

    def _check(self, audio):
        # The hotwords hint makes whisper "hear" BMO in plain noise (door
        # slams, hums), so noise is dropped twice: the VAD filter removes
        # non-speech first, and segments whisper itself rates as probably
        # not speech are ignored. (Tested: noise scores >= 0.5, speech ~0.01.)
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            condition_on_previous_text=False, without_timestamps=True,
            hotwords=self.hotwords)
        text = " ".join(s.text.strip() for s in segments
                        if s.no_speech_prob < 0.4).strip()
        if self.debug and text:
            print(f"\n[wake heard: {text!r}]")
            reprompt()
        rest = self.match(text)
        if rest is None:
            return False
        self.cooldown_until = time.time() + 2.0
        self.agent.on_wake(rest)
        return True


def chime():
    """A quick two-note 'I'm listening' sound (Windows only; silent elsewhere)."""
    try:
        import winsound
        winsound.Beep(880, 70)
        winsound.Beep(1320, 90)
    except Exception:
        pass


def breathe(face, rounds=4, inhale=4, hold=4, exhale=4, say=None):
    """Guided box breathing, on the main-ish thread: a soft chime and a
    printed cue for each phase ('breathe in... hold... breathe out...'), and
    the face holds its calm 'breathing' state. Works with no voice at all -
    the terminal is the guide. Returns after `rounds` cycles."""
    face.set_state("breathing")
    print("\n[breathing] Let's take a few slow breaths together. Follow the "
          "words (or just listen to the chimes).")
    for r in range(rounds):
        for label, secs, tone in (("breathe in", inhale, 660),
                                  ("hold", hold, 880),
                                  ("breathe out", exhale, 440)):
            if say:
                say(f"{label}")
            chime_soft(tone)
            print(f"  {label}... ({secs}s)   round {r + 1}/{rounds}")
            time.sleep(secs)
    print("[breathing] Nice. Notice how that feels, even a little.\n")


def chime_soft(freq):
    """A single soft beep for the breathing cues (Windows only)."""
    try:
        import winsound
        winsound.Beep(int(freq), 60)
    except Exception:
        pass


# ------------------------------------------------------------------ memory
class Memory:
    """What BMO remembers between runs: the recent conversation plus a list
    of long-term facts. Stored as JSON; writes go to a temp file that then
    replaces the real one, so a crash mid-save can't corrupt it."""

    MAX_HISTORY = 60

    def __init__(self, path, max_history=None):
        self.path = path            # None = remember only until BMO exits
        # 60 messages ~= the last 30 exchanges. Bigger = better recall of the
        # conversation, at the cost of more tokens (and being more likely to
        # push the model's context limit). Override with --max-history.
        if max_history is not None and max_history >= 0:
            self.MAX_HISTORY = max_history
        self.facts = []
        self.history = []
        self._lock = threading.Lock()
        if path and os.path.exists(path):
            self._load()

    def _recent(self):
        """The most recent MAX_HISTORY messages (0 = keep none)."""
        n = self.MAX_HISTORY
        return list(self.history)[-n:] if n > 0 else []

    def trim(self):
        """Drop the oldest messages so only MAX_HISTORY remain."""
        self.history = self._recent()
        return self.history

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.facts = [str(x) for x in data.get("facts", [])]
            self.history = [
                m for m in data.get("history", [])
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
            ]
            self.trim()
        except (OSError, ValueError, AttributeError) as e:
            # keep the unreadable file for inspection instead of overwriting it
            broken = self.path + ".broken"
            try:
                os.replace(self.path, broken)
            except OSError:
                broken = None
            print(f"[!] Couldn't read memory file ({e}). Starting fresh"
                  + (f"; old file kept as {broken}" if broken else "") + ".")

    def save(self):
        if not self.path:
            return
        with self._lock:
            data = {"facts": list(self.facts),
                    "history": self._recent()}
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError as e:
                print(f"\n[!] Couldn't save memory: {e}")
                reprompt()

    def remember(self, fact):
        fact = fact.strip().rstrip(".!").strip()
        if not fact or fact.lower() in (f.lower() for f in self.facts):
            return False
        self.facts.append(fact)
        self.save()
        return True

    def forget(self, index=None):
        """Forget one fact (1-based index), or all of them if index is None."""
        if index is None:
            self.facts = []
        else:
            del self.facts[index - 1]
        self.save()

    # Facts are kept in the user's words ("my name is Sam") but given to the
    # model in third person - small models confuse whose "my" it is otherwise.
    THIRD_PERSON = [
        (r"\bI am\b|\bI'm\b", "the user is"),
        (r"\bI have\b|\bI've\b", "the user has"),
        (r"\bI\b", "the user"),
        (r"\bmy\b|\bmine\b", "the user's"),
        (r"\bme\b", "the user"),
    ]

    @classmethod
    def third_person(cls, fact):
        for pat, rep in cls.THIRD_PERSON:
            fact = re.sub(pat, rep, fact, flags=re.I)
        return fact[:1].upper() + fact[1:]

    def system_prompt(self, base):
        if not self.facts:
            return base
        return (base + "\n\nWhat you know about the user:\n"
                + "\n".join(f"- {self.third_person(f)}" for f in self.facts))


# ----------------------------------------------------------------- wellness
class Wellness:
    """BMO's gentle mental-health side: a daily mood tracker, a gratitude
    log, a private journal, and guided breathing. Backed by one JSON file so
    entries survive restarts, and written the same careful way Memory is
    (temp file, then replace, so a crash can't corrupt it).

    This is a supportive friend, not a therapist: it notices, reflects and
    offers small coping ideas, and hands off to real crisis lines when
    something serious comes up (see CRISIS_PATTERNS)."""

    MAX_ENTRIES = 400              # trim oldest check-ins beyond this

    # One small, concrete thing to try. Picked to match the feeling, never
    # presented as a cure - just an option ("want to try...?").
    COPING = {
        "anxious": ["a slow box-breath (try /breathe)", "the 5-4-3-2-1 "
                    "grounding trick (name 5 things you can see, 4 you can "
                    "touch, 3 you can hear, 2 you can smell, 1 you can "
                    "taste)", "splashing cold water on your face or holding "
                    "something cold"],
        "sad": ["getting some daylight or a short walk", "texting one "
                "person who makes you feel safe", "putting on one song you "
                "loved when you were younger"],
        "angry": ["moving your body hard for two minutes", "slow breathing "
                  "with a longer out-breath (in for 4, out for 6)", "writing "
                  "the unedited version in /journal, then closing it"],
        "tired": ["a real break - water, a stretch, five minutes away from "
                  "screens", "lowering the bar for the rest of today on "
                  "purpose"],
        "lonely": ["sending one small message to someone ('hey, thinking of "
                   "you')", "going somewhere with other people nearby - a "
                   "cafe, a library, a park"],
        "overwhelmed": ["picking just the very next tiny step, nothing "
                        "further", "writing everything down so it's out of "
                        "your head (or /journal it)", "naming the one thing "
                        "that actually has to happen today"],
        "default": ["a glass of water and a few slow breaths", "naming out "
                    "loud what you're feeling - actually saying it helps",
                    "a two-minute break with no screens"],
    }
    # words -> a COPING key
    FEELING_WORDS = [
        ("anxious", r"\b(anxious|anxiety|panic\w*|nervous|worried|scared|"
                    r"afraid|on edge|overthink\w*|restless)\b"),
        ("sad", r"\b(sad|down|low|depress\w*|crying|cry|miserable|hurt|"
                r"lonely|alone|grief|loss|miss\w*)\b"),
        ("angry", r"\b(angry|mad|furious|irritat\w*|frustrat\w*|annoy\w*|"
                  r"rage|unfair|resent\w*)\b"),
        ("tired", r"\b(tired|exhaust\w*|burn\w* ?out|drained|no energy|"
                  r"can'?t sleep|insomnia|weary|worn out)\b"),
        ("overwhelmed", r"\b(overwhelm\w*|too much|can'?t cope|drowning|"
                        r"stressed|stress|burnt out|burned out|spinning)\b"),
        ("lonely", r"\b(lonely|alone|no ?one|isolat\w*|left out|unloved)\b"),
    ]

    def __init__(self, path, name="BMO"):
        self.path = path            # None = keep entries only for this run
        self.name = name
        self.moods = []             # [{date, time, day, score, note}]
        self.gratitude = []         # [{date, text}]
        self.journal = []           # [{date, time, text}]
        self.last_prompt_day = None   # the day we last asked for a check-in
        self._lock = threading.Lock()
        if path and os.path.exists(path):
            self._load()

    # ---- storage ----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.moods = [e for e in data.get("moods", [])
                          if isinstance(e, dict) and "score" in e]
            self.gratitude = [e for e in data.get("gratitude", [])
                              if isinstance(e, dict) and e.get("text")]
            self.journal = [e for e in data.get("journal", [])
                            if isinstance(e, dict) and e.get("text")]
            self.last_prompt_day = data.get("last_prompt_day")
        except (OSError, ValueError, AttributeError) as e:
            broken = self.path + ".broken"
            try:
                os.replace(self.path, broken)
            except OSError:
                broken = None
            print(f"[!] Couldn't read wellness file ({e}). Starting fresh"
                  + (f"; old file kept as {broken}" if broken else "") + ".")

    def save(self):
        if not self.path:
            return
        with self._lock:
            data = {"moods": self.moods[-self.MAX_ENTRIES:],
                    "gratitude": self.gratitude[-self.MAX_ENTRIES:],
                    "journal": self.journal[-self.MAX_ENTRIES:],
                    "last_prompt_day": self.last_prompt_day}
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError as e:
                print(f"\n[!] Couldn't save wellness file: {e}")

    # ---- helpers ----
    @staticmethod
    def parse_score(text):
        """A mood score 1-5 out of what the user typed/said, or None.
        Numbers win ('4', 'mood 4 out of 5'); otherwise the words in
        MOOD_WORDS are checked, warmest feeling first."""
        t = (text or "").strip().lower()
        if not t:
            return None
        m = re.search(r"\b([1-5])(?:\s*(?:/|out of)\s*5)?\b", t)
        if m:
            return int(m.group(1))
        for score, pat in reversed(MOOD_WORDS):     # 5 first, then down
            if re.search(pat, t):
                return score
        return None

    @staticmethod
    def is_crisis(text):
        return bool(text) and any(re.search(p, text, re.I)
                                  for p in CRISIS_PATTERNS)

    def feeling(self, text):
        """Which COPING key fits a free-text feeling (for coping tips)."""
        for key, pat in self.FEELING_WORDS:
            if re.search(pat, text or "", re.I):
                return key
        return "default"

    # ---- mood tracker ----
    def log_mood(self, text="", when=None):
        """Save one check-in. `text` may hold a score and/or a note, e.g.
        '4', 'pretty good', '2 - long day'. Returns a friendly summary line,
        or None if no score/note could be found."""
        text = (text or "").strip()
        score = self.parse_score(text)
        # the note is whatever is left once any leading score is removed
        note = re.sub(r"^[\s\-:]*\b[1-5]\b(?:\s*(?:/|out of)\s*5)?[\s\-:]*",
                      "", text).strip() if score else text
        if score is None and not note:
            return None
        now = time.localtime(when if when else time.time())
        entry = {"date": time.strftime("%Y-%m-%d", now),
                 "time": time.strftime("%H:%M", now),
                 "day": time.strftime("%A", now),
                 "score": score,
                 "note": note[:280]}
        self.moods.append(entry)
        self.moods = self.moods[-self.MAX_ENTRIES:]
        self.last_prompt_day = entry["date"]
        self.save()
        return self.describe(entry)

    @staticmethod
    def describe(entry):
        """'Mood today: good (4/5)' plus the note, if any."""
        s = entry.get("score")
        label = MOOD_LABELS.get(s, "noted") if s else "noted"
        head = f"Mood {entry['date']}: {label}" + (f" ({s}/5)" if s else "")
        return head + (f" - {entry['note']}" if entry.get("note") else "")

    def recent_moods(self, n=7):
        return self.moods[-n:]

    def _streak(self):
        """How many days in a row (ending today/yesterday) there's a check-in."""
        days = sorted({e["date"] for e in self.moods})
        if not days:
            return 0
        today = time.strftime("%Y-%m-%d")
        yest = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        if days[-1] not in (today, yest):
            return 0
        streak = 1
        for i in range(len(days) - 1, 0, -1):
            d0 = time.mktime(time.strptime(days[i], "%Y-%m-%d"))
            d1 = time.mktime(time.strptime(days[i - 1], "%Y-%m-%d"))
            if round((d0 - d1) / 86400) == 1:
                streak += 1
            else:
                break
        return streak

    def mood_summary(self, n=7):
        """A few plain lines about the last n check-ins - a tiny trend,
        never a diagnosis."""
        recent = self.recent_moods(n)
        if not recent:
            return ("I don't have any mood check-ins yet. Try typing a number "
                    "1-5 (1 = rough, 5 = great), or just say how today felt.")
        scored = [e["score"] for e in recent if e["score"]]
        lines = [f"Last {len(recent)} check-in(s):"]
        for e in recent:
            label = MOOD_LABELS.get(e["score"], "-") if e["score"] else "-"
            bar = "#" * (e["score"] or 0)
            lines.append(f"  {e['date']} ({e['day'][:3]})  {label:<5} "
                         f"{bar:<5} "
                         + (e["note"] if e["note"] else ""))
        if scored:
            avg = sum(scored) / len(scored)
            lines.append(f"  average {avg:.1f}/5 over {len(scored)} scored "
                         f"day(s)")
        streak = self._streak()
        if streak:
            lines.append(f"  {streak}-day check-in streak - nice going.")
        return "\n".join(lines)

    def should_check_in(self):
        """True if BMO should gently ask for today's mood: no check-in yet
        today, and it hasn't already asked today."""
        today = time.strftime("%Y-%m-%d")
        if any(e["date"] == today for e in self.moods):
            return False
        return self.last_prompt_day != today

    def mark_prompted(self):
        self.last_prompt_day = time.strftime("%Y-%m-%d")
        self.save()

    def context(self):
        """A short wellness note for the system prompt, so BMO can refer to
        the user's recent moods naturally. Empty if there's nothing yet."""
        if not self.moods and not self.gratitude:
            return ""
        bits = []
        recent = self.recent_moods(5)
        scored = [e["score"] for e in recent if e["score"]]
        if scored:
            avg = sum(scored) / len(scored)
            bits.append(f"their last {len(scored)} mood check-in(s) averaged "
                        f"{avg:.1f}/5")
        if self.moods and self.moods[-1].get("note"):
            bits.append(f"their latest note was \"{self.moods[-1]['note']}\"")
        if self.gratitude:
            bits.append(f"they've logged {len(self.gratitude)} gratitude "
                        "note(s)")
        if not bits:
            return ""
        return ("\n\nSomething you quietly keep track of for the user: "
                + "; ".join(bits) + ". You can mention it gently if it fits, "
                "but never nag about it or bring it up out of nowhere.")

    # ---- gratitude + journal ----
    def add_gratitude(self, text):
        text = (text or "").strip()
        if not text:
            return None
        self.gratitude.append({"date": time.strftime("%Y-%m-%d"), "text": text[:280]})
        self.save()
        return text

    def add_journal(self, text):
        text = (text or "").strip()
        if not text:
            return None
        now = time.localtime()
        self.journal.append({"date": time.strftime("%Y-%m-%d", now),
                             "time": time.strftime("%H:%M", now),
                             "text": text[:2000]})
        self.journal = self.journal[-self.MAX_ENTRIES:]
        self.save()
        return text

    # ---- coping suggestions (plain rules, no model needed) ----
    def suggest(self, feeling_text=""):
        key = self.feeling(feeling_text)
        ideas = self.COPING.get(key, self.COPING["default"])
        pick = random.choice(ideas)
        lead = {"anxious": "That tight, racing feeling is really hard.",
                "sad": "That sounds heavy.",
                "angry": "That would wind anyone up.",
                "tired": "Sounds like you're running on empty.",
                "overwhelmed": "Yeah, that's a lot to carry at once.",
                "lonely": "That's a hard place to be in.",
                "default": "Thanks for telling me."}.get(key, "Thanks for telling me.")
        return f"{lead} Want to try {pick}?"

    @staticmethod
    def is_negative(text):
        """True for low/anxious wording - used to decide on a caring face."""
        return any(re.search(p, text or "", re.I)
                   for _, p in MOOD_WORDS if _ <= 2)


# ------------------------------------------------------------------- tools
def _tool(tool_name, description, /, **params):
    """Build an Ollama tool schema. params: name=(type, description)."""
    return {"type": "function", "function": {
        "name": tool_name, "description": description,
        "parameters": {
            "type": "object",
            "properties": {k: {"type": t, "description": d}
                           for k, (t, d) in params.items()},
            "required": list(params),
        }}}


class Tools:
    """Things BMO can do besides talk. The model asks for a tool by name, we
    run it here and hand the result back to the model as text."""

    MAX_OUTPUT = 1500               # characters of tool output given to the model
    # appended to lookup results - small models follow a hint next to the
    # data much better than one in the system prompt
    SPOKEN = ("\n\n(Answer only what the user asked, in 1-2 short spoken "
              "sentences. No lists, no links, no markdown.)")
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
    # friendly names -> what Windows can actually launch
    APPS = {"calculator": "calc", "notepad": "notepad", "paint": "mspaint",
            "file explorer": "explorer", "files": "explorer",
            "explorer": "explorer", "settings": "ms-settings:",
            "task manager": "taskmgr", "command prompt": "cmd",
            "terminal": "wt", "powershell": "powershell",
            "spotify": "spotify:", "camera": "microsoft.windows.camera:",
            "clock": "ms-clock:", "alarms": "ms-clock:", "mail": "outlookmail:",
            "calendar": "outlookcal:", "store": "ms-windows-store:"}

    PROMPT = (
        "\n\nYou have tools. For weather use get_weather; give it an empty "
        "place for the user's own location (never ask where they are). Your "
        "own knowledge stops in 2023, so for anything that happened after that, news, "
        "sports results, prices, or any fact you are not sure about, ALWAYS "
        "call web_search first (for example: who won a game, match or "
        "election, what something costs, or anything dated 2024 or later), "
        "then "
        "answer from the results in your own short words (don't read out web "
        "addresses). Use open_website or open_app when the user asks you to "
        "open something. When the user wants something done or checked on "
        "their computer (files, disk space, battery, settings...), call "
        "run_command. Never write a command in your reply or ask for "
        "permission yourself: just call run_command, and the app will ask "
        "the user. For normal chat, answer without tools. Always reply in "
        "English unless the user writes in another language. When the user "
        "shares how they're feeling or something hard that happened, do NOT "
        "reach for a tool first - just respond warmly like a friend. Use "
        "log_mood only when they clearly give a mood to record, and "
        "suggest_coping only if they seem to want help settling a feeling "
        "(never for ordinary sadness - sometimes they just want to be heard).")

    def __init__(self, face, allow_shell=True, wellness=None):
        self.face = face
        self.allow_shell = allow_shell
        self.wellness = wellness
        # real paths, so the model doesn't invent C:\Users\YourUsername
        home = os.path.expanduser("~")
        desktop = next((d for d in (os.path.join(home, "OneDrive", "Desktop"),
                                    os.path.join(home, "Desktop"))
                        if os.path.isdir(d)), home)
        self.PROMPT = self.PROMPT + (
            f" The computer runs {'Windows' if os.name == 'nt' else sys.platform}"
            f"; the user's home folder is {home} and their desktop is {desktop}.")
        self.funcs = {"web_search": self.web_search,
                      "get_weather": self.get_weather,
                      "open_website": self.open_website,
                      "open_app": self.open_app}
        self.schemas = [
            _tool("web_search", "Search the web. Returns the top results "
                  "with short snippets.",
                  query=("string", "what to search for")),
            _tool("get_weather", "Current weather and a 3-day forecast.",
                  place=("string", "city or place; empty string for where "
                                   "the user is")),
            _tool("open_website", "Open a web page in the user's browser.",
                  url=("string", "the address, e.g. https://youtube.com")),
            _tool("open_app", "Open an app on the user's computer.",
                  name=("string", "the app, e.g. notepad, calculator, "
                                  "spotify, settings")),
        ]
        if allow_shell:
            self.funcs["run_command"] = self.run_command
            self.schemas.append(_tool(
                "run_command", "Run a PowerShell command on the user's "
                "computer and get its output. The user is asked to approve "
                "it first." if os.name == "nt" else
                "Run a shell command on the user's computer and get its "
                "output. The user is asked to approve it first.",
                command=("string", "the command to run")))
        if wellness is not None:
            self.funcs["log_mood"] = self.log_mood
            self.funcs["suggest_coping"] = self.suggest_coping
            self.schemas += [
                _tool("log_mood", "Record the user's mood in their daily "
                      "mood tracker. Use only when they clearly say how "
                      "they're feeling.",
                      mood=("string", "their mood, e.g. '4', 'pretty good', "
                                      "or '2 - rough day'")),
                _tool("suggest_coping", "Offer one small, concrete coping "
                      "idea when the user wants help settling a hard "
                      "feeling. Not for ordinary sadness.",
                      feeling=("string", "how they're feeling, in their "
                                         "words")),
            ]
        self.schemas_by_name = {s["function"]["name"]: s["function"]
                                for s in self.schemas}

    @property
    def names(self):
        return list(self.funcs)

    def run(self, name, args, confirm):
        """Run one tool call. Never raises - errors go back to the model as
        text so it can explain or try something else."""
        fn = self.funcs.get(name)
        if fn is None:
            return f"Error: there is no tool called {name!r}."
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        # each tool takes one argument; small models sometimes misname it,
        # so fall back to the first value given
        param = next(iter(self.schemas_by_name[name]["parameters"]["properties"]))
        value = args.get(param, next(iter(args.values()), ""))
        try:
            if name == "run_command":
                return fn(str(value), confirm)
            return fn(str(value))
        except Exception as e:
            return f"Error: {name} failed: {e}"

    # ---- the tools ----
    def web_search(self, query=""):
        query = query.strip()
        if not query:
            return "Error: empty search query."
        print(f"[searching the web: {query}]")
        self.face.set_state("working", caption="searching the web...")
        try:
            results = self._duckduckgo(query)
        except Exception as e:
            results, err = [], e
        else:
            err = None
        if not results:                 # DuckDuckGo blocked or empty: try Wikipedia
            try:
                results = self._wikipedia(query)
            except Exception as e:
                err = err or e
        if not results:
            return f"No results found{f' ({err})' if err else ''}."
        text = "\n".join(f"{i}. {title} ({url})\n   {snip}"
                         for i, (title, url, snip) in enumerate(results[:5], 1))
        return text[:self.MAX_OUTPUT * 2] + self.SPOKEN

    def _get(self, url, data=None):
        req = urllib.request.Request(url, data=data,
                                     headers={"User-Agent": self.UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", "replace")

    @staticmethod
    def _strip_html(s):
        return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()

    def _duckduckgo(self, query):
        page = self._get("https://html.duckduckgo.com/html/",
                         urllib.parse.urlencode({"q": query}).encode())
        links = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            page, re.S)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
        out = []
        for (url, title), snip in zip(links, snips):
            m = re.search(r"uddg=([^&]+)", url)     # unwrap DDG's redirect link
            url = urllib.parse.unquote(m.group(1)) if m else url
            if "duckduckgo.com/y.js" in url:         # skip ads
                continue
            out.append((self._strip_html(title), url, self._strip_html(snip)))
        return out

    def _wikipedia(self, query):
        data = json.loads(self._get(
            "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": query,
                 "srlimit": 3, "format": "json"})))
        return [(r["title"],
                 "https://en.wikipedia.org/wiki/" + r["title"].replace(" ", "_"),
                 self._strip_html(r["snippet"]))
                for r in data.get("query", {}).get("search", [])]

    def get_weather(self, place=""):
        place = place.strip()
        if place.lower() in ("here", "my location", "current location", "me"):
            place = ""
        print(f"[checking the weather{' in ' + place if place else ''}]")
        self.face.set_state("working", caption="checking the weather...")
        d = json.loads(self._get("https://wttr.in/"
                                 + urllib.parse.quote(place) + "?format=j1"))
        area = d["nearest_area"][0]
        where = ", ".join(v for v in (area["areaName"][0]["value"],
                                      area["region"][0]["value"],
                                      area["country"][0]["value"]) if v)
        c = d["current_condition"][0]
        lines = [f"Weather for {where}:",
                 f"Now: {c['weatherDesc'][0]['value']}, {c['temp_C']}C / "
                 f"{c['temp_F']}F (feels like {c['FeelsLikeC']}C / "
                 f"{c['FeelsLikeF']}F), humidity {c['humidity']}%, wind "
                 f"{c['windspeedKmph']} km/h"]
        for day in d["weather"][:3]:
            noon = day["hourly"][len(day["hourly"]) // 2]
            lines.append(f"{day['date']}: {noon['weatherDesc'][0]['value'].strip()}, "
                         f"high {day['maxtempC']}C / {day['maxtempF']}F, "
                         f"low {day['mintempC']}C / {day['mintempF']}F, "
                         f"chance of rain {noon['chanceofrain']}%")
        return "\n".join(lines) + self.SPOKEN

    def log_mood(self, mood=""):
        """Wellness tool: save a mood check-in."""
        summary = self.wellness.log_mood(mood)
        if summary is None:
            return ("Couldn't find a mood (1-5) in that. Ask the user how "
                    "they're feeling, in their own words.")
        self.face.set_state("concerned", hold=3)
        print(f"[mood logged: {summary}]")
        return (f"Saved: {summary}. Acknowledge it warmly in one short "
                "sentence - don't repeat the number back like a form.")

    def suggest_coping(self, feeling=""):
        """Wellness tool: one small coping idea for how they feel."""
        tip = self.wellness.suggest(feeling)
        self.face.set_state("concerned", hold=3)
        print(f"[coping suggestion: {tip}]")
        return (tip + self.SPOKEN + " Offer it as an option, not an "
                "instruction, and keep it kind.")

    def open_website(self, url=""):
        url = url.strip()
        if not re.match(r"https?://", url, re.I):
            if "://" in url:
                return "Error: only http and https web addresses can be opened."
            url = "https://" + url
        print(f"[opening {url}]")
        self.face.set_state("working", caption="opening website...")
        webbrowser.open(url)
        return f"Opened {url} in the browser."

    def open_app(self, name=""):
        name = name.strip()
        if not name or re.search(r"[\\/]|\.\.", name):
            return "Error: give an app name like notepad, not a file path."
        target = self.APPS.get(name.lower(), name)
        print(f"[opening app: {name}]")
        self.face.set_state("working", caption=f"opening {name}...")
        if os.name == "nt":
            try:
                os.startfile(target)
            except OSError:
                return (f"Error: couldn't find an app called {name!r}. "
                        "It may not be installed.")
        else:
            subprocess.Popen(["open", "-a", name] if sys.platform == "darwin"
                             else [target])
        return f"Opened {name}."

    def run_command(self, command, confirm):
        command = command.strip()
        if not command:
            return "Error: empty command."
        if not confirm(f"I want to run this command:\n    {command}"):
            print("[not run]")
            return ("Declined: the user chose not to run this command. Reply "
                    "with just a short OK, like: Okay, I won't run it.")
        print(f"[running: {command}]")
        self.face.set_state("working", caption="running command...")
        shell = (["powershell", "-NoProfile", "-NonInteractive", "-Command",
                  command] if os.name == "nt" else ["bash", "-c", command])
        try:
            p = subprocess.run(shell, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
        except subprocess.TimeoutExpired:
            return "Error: the command took longer than 30 seconds and was stopped."
        out = (p.stdout + p.stderr).strip() or "(no output)"
        if len(out) > self.MAX_OUTPUT:
            out = out[:self.MAX_OUTPUT] + "\n...(cut off)"
        print("\n".join("    " + ln for ln in out.splitlines()[:15]))
        return f"exit code {p.returncode}\n{out}"


# ------------------------------------------------------------------- agent
class Agent:
    MAX_TOOL_ROUNDS = 4     # tool calls BMO may chain before it must answer

    def __init__(self, face, model, host, name, system, speaker, listener,
                 memory, tools=None, convo=False, wellness=None):
        self.face, self.model, self.host, self.name = face, model, host, name
        self.system = system
        self.speaker = speaker
        self.listener = listener
        self.memory = memory
        self.wellness = wellness        # None = mental-health support off
        self.tools = tools              # None = chat only
        self.wake = None                # WakeWord detector, if on
        self.awaiting_answer = False    # a yes/no question is open
        self.key_answer = None          # Y/N pressed in the face window
        self.convo = convo              # hands-free: keep listening after replies
        self.turn_lock = threading.Lock()   # one conversation turn at a time
        self.cancel = threading.Event()     # set to interrupt a reply
        self.busy = False
        self.just_remembered = False
        self._last_key = 0.0
        # "remember (that) ..." / "BMO, please remember ..." - typed or spoken.
        # Not "remember when/what/..." (those are questions about the past).
        self.remember_re = re.compile(
            r"^\W*(?:(?:hey|ok|okay)\W+)?(?:" + re.escape(name) + r"\W+)?"
            r"(?:please\s+)?((?:can|could|will) you\s+)?remember\s+"
            r"(?:that\s+)?(?!(?:when|what|how|who|where|why|if|me)\b)(.{3,}?)"
            r"\W*$", re.I)

    # the conversation lives in the memory object so it gets saved
    @property
    def history(self):
        return self.memory.history

    @history.setter
    def history(self, value):
        self.memory.history = value

    def clear(self):
        self.history = []
        self.memory.save()

    def _check_remember(self, text):
        """If the user asked BMO to remember something, store it as a fact."""
        m = self.remember_re.match(text)
        if not m:
            return
        # "remember my name?" is a question; "can you remember that ...?" isn't
        if text.rstrip().endswith("?") and not m.group(1):
            return
        if self.memory.remember(m.group(2)):
            self.just_remembered = True
            print(f"[remembered: {self.memory.facts[-1]}]")

    # ---- mental-health support ----
    def _crisis(self, text):
        """True if what the user said sounds like a crisis. When it does, BMO
        stops being clever, says the human reply from CRISIS_REPLY, and does
        not hand it to the model to improvise around."""
        return self.wellness is not None and self.wellness.is_crisis(text)

    def _say_crisis(self, voice=False):
        """Deliver the fixed, careful crisis reply (spoken or printed)."""
        self.face.set_state("concerned", hold=8)
        print(f"\n{self.name}> ")
        for line in CRISIS_REPLY.splitlines():
            print(line)
        self.history += [{"role": "user",
                          "content": "(the user may be in crisis)"},
                         {"role": "assistant",
                          "content": "I'm here with you. Please reach out to "
                          "988 (call/text), text HOME to 741741, or "
                          "findahelpline.com - and tell someone you trust. "
                          "You matter."}]
        if self.speaker.active and not voice:
            for sentence in CRISIS_REPLY.splitlines()[:3]:
                if sentence.strip():
                    self.speaker.say(self._clean_for_speech(sentence))
            self.speaker.wait_done()
        self.memory.save()

    def _wellness_offer(self, voice=False):
        """A gentle first-message nudge: if there's no check-in today, BMO
        quietly invites one. Never pushy, and easy to ignore."""
        w = self.wellness
        if w is None or not w.should_check_in():
            return
        w.mark_prompted()
        line = ("By the way, how's your day going? A 1-5 is plenty - "
                "1 is rough, 5 is great. (/mood logs it, /moods shows the "
                "trend)")
        print(f"\n{self.name}> {line}")
        if self.speaker.active:
            self.speaker.say(self._clean_for_speech(
                "By the way, how's your day going?"))

    # ---- startup ----
    def warm_up(self):
        """Load the model into memory in the background so the first real
        message is fast. Runs in its own thread while the face 'wakes up'."""
        self._check_tools()
        started = time.time()
        body = json.dumps(
            {"model": self.model, "keep_alive": KEEP_ALIVE}  # no prompt = just load
        ).encode()
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                resp.read()
            secs = time.time() - started
            print(f"\n[{self.name} is ready - model loaded in {secs:.0f}s]")
            reprompt()
            # only greet if the user hasn't started chatting already
            if self.face.state == "waking":
                self.speaker.ready.wait(15)
                if self.speaker.active:
                    self.speaker.say(random.choice(GREETINGS))
                    self.speaker.wait_done()
                if self.face.state in ("waking", "speaking"):
                    self.face.set_state("happy", hold=1.5)
        except urllib.error.HTTPError as e:
            msg = (f"model '{self.model}' not found. Run: ollama pull {self.model}"
                   if e.code == 404 else f"Ollama returned HTTP {e.code}")
            self._warm_fail(msg)
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            self._warm_fail(f"can't reach Ollama at {self.host}. "
                            "Open the Ollama app (or run: ollama serve).")

    def _check_tools(self):
        """Turn tools off (with a note) if the model can't call them."""
        if not self.tools:
            return
        try:
            req = urllib.request.Request(
                f"{self.host}/api/show",
                data=json.dumps({"model": self.model}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                caps = json.loads(resp.read()).get("capabilities")
        except Exception:
            return              # can't tell; _stream falls back if needed
        if caps is not None and "tools" not in caps:
            self._tools_off()

    def _tools_off(self):
        self.tools = None
        print(f"\n[{self.model} can't use tools, so web search / apps / "
              "commands are off. For tools try: --model qwen2.5:3b]")
        reprompt()

    def _warm_fail(self, msg):
        print(f"\n[!] Warm-up failed: {msg}")
        reprompt()
        if self.face.state == "waking":
            self.face.set_state("error", hold=4)

    # ---- text chat ----
    def say(self, user_text):
        with self.turn_lock:
            self._reply(user_text)

    def _clean_for_speech(self, text):
        text = re.sub(r"[*_`#>~|]+", "", text)
        text = re.sub("[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text)
        return text.strip()

    def _speak(self, sentence):
        """One finished sentence of a reply: speak it with its face, or when
        muted just queue its face (no audio to sync to)."""
        sentence = self._clean_for_speech(sentence)
        if re.search(r"\w", sentence):
            mood = classify_sentence(sentence)
            if self._voiced:
                self.speaker.say(sentence, mood)
            else:
                self.face.queue_expression(mood)

    def _speak_complete_sentences(self, text):
        """Speak every finished sentence in text; return the unfinished tail."""
        parts = re.split(r"(?<=[.!?])\s+|\n+", text)
        for p in parts[:-1]:
            self._speak(p)
        return parts[-1]

    def _reply(self, user_text, voice=False):
        self.busy = True
        self.cancel.clear()
        self.just_remembered = False
        if self._crisis(user_text):
            # Something serious: don't let the model improvise around it.
            self._say_crisis(voice)
            self.busy = False
            self.memory.save()
            return
        self._check_remember(user_text)
        self.history.append({"role": "user", "content": user_text})
        self.history = self.memory.trim()
        now = time.strftime("%A, %B %d, %Y, %I:%M %p")
        system = self.memory.system_prompt(
            f"Your name is {self.name}. It is now {now}. " + self.system
            + (self.tools.PROMPT if self.tools else "")
            + (self.wellness.context() if self.wellness else "")
            # small models copy an example far better than they follow a rule
            + f"\n\nStay in character as {self.name}: you have your own likes, "
            "feelings and opinions, and never say you are an AI or a program. "
            "For example, if asked 'what's your favorite color?' you might say "
            "'Ooh, teal! It's the color of my case, and it makes me feel cozy.'")
        # tool calls and results live only in this turn's messages; history
        # keeps just what was said, so it stays small and saves cleanly
        messages = [{"role": "system", "content": system}] + self.history

        self.face.set_state("thinking")
        self._voiced = self.speaker.active
        reply, nudged = [], False
        try:
            for rnd in range(self.MAX_TOOL_ROUNDS + 1):
                # last round: no tools, so the model has to answer
                text, calls = self._stream(messages,
                                           rnd < self.MAX_TOOL_ROUNDS)
                if not text.strip() and not calls and not self.cancel.is_set():
                    # small models occasionally return nothing at all;
                    # one retry without tools almost always gets an answer
                    text, calls = self._stream(messages, False)
                if text.strip():
                    reply.append(text.strip())
                if (not calls and self.tools and not nudged
                        and rnd < self.MAX_TOOL_ROUNDS
                        and not self.cancel.is_set()
                        and re.search(r"\b(let me|i'?ll|i will|i can)\s+"
                                      r"(check|search|look|find)|"
                                      r"\brun (the|this) command\b|`[^`]{4,}`",
                                      text, re.I)):
                    # it said it would look something up, or told the user to
                    # run a command, instead of calling a tool: nudge once
                    nudged = True
                    messages += [{"role": "assistant", "content": text},
                                 {"role": "user",
                                  "content": "(Yes, go ahead and use your tools.)"}]
                    continue
                if not calls or self.cancel.is_set():
                    break
                messages.append({"role": "assistant", "content": text,
                                 "tool_calls": calls})
                for call in calls:
                    if self.cancel.is_set():
                        break
                    fn = call.get("function", {})
                    result = self.tools.run(
                        fn.get("name"), fn.get("arguments") or {},
                        confirm=lambda q: self.confirm(q, voice))
                    messages.append({"role": "tool", "tool_name": fn.get("name"),
                                     "content": result})
                self.face.set_state("thinking")

            if reply:
                self.history.append({"role": "assistant",
                                     "content": " ".join(reply)})
            elif self.history and self.history[-1]["role"] == "user":
                self.history.pop()

            if self.cancel.is_set():
                print("[interrupted]")
            else:
                if self._voiced:  # wait for the speech to finish (unless interrupted)
                    while not self.speaker.idle.wait(0.1):
                        if self.cancel.is_set():
                            break
                else:   # muted: let each sentence's face have its moment
                    while (self.face.expressions_pending()
                           and not self.cancel.is_set()):
                        time.sleep(0.1)
                if not self.cancel.is_set():
                    mood = ("remembering" if self.just_remembered
                            else pick_mood(user_text, " ".join(reply)))
                    # if the user sounded low, sit with them instead of
                    # flashing a cheerful face back at them
                    if (mood not in ("sad", "love") and self.wellness
                            and self.wellness.is_negative(user_text)):
                        mood = "concerned"
                    self.face.set_state(mood, hold=2.5)
        except urllib.error.HTTPError as e:
            self.face.set_state("error", hold=4)
            if e.code == 404:
                print(f"\n[!] Model '{self.model}' not found. "
                      f"Run: ollama pull {self.model}")
            else:
                print(f"\n[!] Ollama returned HTTP {e.code}")
            self.history.pop()
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            self.face.set_state("error", hold=4)
            print(f"\n[!] Can't reach Ollama at {self.host}. "
                  "Is it running? (try: ollama serve)")
            self.history.pop()
        finally:
            self.busy = False
            self.memory.save()

    def _stream(self, messages, allow_tools=True):
        """One request to the model: prints and speaks the text as it
        streams in. Returns (text, tool_calls)."""
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "keep_alive": KEEP_ALIVE}
        if allow_tools and self.tools:
            payload["tools"] = self.tools.schemas
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 400 and "tools" in payload and "tool" in detail:
                self._tools_off()           # model can't do tools: chat instead
                return self._stream(messages, False)
            raise

        text, calls, started, buf = [], [], False, ""
        with resp:
            for raw in resp:
                if self.cancel.is_set():
                    break
                if not raw.strip():
                    continue
                data = json.loads(raw)
                msg = data.get("message", {})
                calls += msg.get("tool_calls") or []
                tok = msg.get("content", "")
                if tok:
                    if not started:
                        started = True
                        print(f"{self.name}> ", end="", flush=True)
                        if not self._voiced:
                            self.face.set_state("speaking")
                    print(tok, end="", flush=True)
                    text.append(tok)
                    buf = self._speak_complete_sentences(buf + tok)
                    if not self._voiced:
                        self.face.talk(len(tok))
                if data.get("done"):
                    break
        if buf.strip() and not self.cancel.is_set():
            self._speak(buf)
        if started:
            print()
        return "".join(text), calls

    # ---- yes/no questions (used before running commands) ----
    def confirm(self, question, voice=False):
        """Ask the user to approve something. Typed turns answer in the
        terminal; voice turns answer out loud or with Y/N in the face window.
        Anything unclear counts as no."""
        if self.cancel.is_set():
            return False
        print(f"\n[?] {question}")
        self.face.set_state("asking")
        if not voice:
            try:
                ans = input("    OK? (y/n) ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            return ans in ("y", "yes")

        self.key_answer, self.awaiting_answer = None, True
        try:
            if self.speaker.active:
                self.speaker.say("I need your OK to run a command. "
                                 "It's shown in the terminal. Should I do it?")
                self.speaker.wait_done(30)
                self.face.set_state("asking")
            print("    say yes or no (or press Y / N in the face window)")
            heard = ""
            L = self.listener
            if L is not None and L.ok:
                if self.key_answer is None:
                    self.face.set_state("listening", caption="yes or no?")
                    audio = L.record(max_secs=6, wait_secs=6)
                    if self.key_answer is None and audio is not None:
                        heard = L.transcribe(audio)
            else:                            # no mic: wait for a key press
                t_end = time.time() + 20
                while self.key_answer is None and time.time() < t_end:
                    time.sleep(0.1)
            if self.key_answer is not None:
                ok = self.key_answer
            else:
                ok = bool(re.match(r"\W*(yes|yeah|yep|yup|sure|ok(ay)?|do it|"
                                   r"go ahead|please)\b", heard, re.I))
                print(f"    heard: {heard!r}")
            print("    -> " + ("yes" if ok else "no"))
            self.face.set_state("thinking")
            return ok
        finally:
            self.awaiting_answer = False

    def answer_key(self, yes):
        """Y / N pressed in the face window."""
        if self.awaiting_answer:
            self.key_answer = yes
            if self.listener is not None:
                self.listener.stop_flag.set()

    # ---- voice chat ----
    def interrupt(self):
        """Stop BMO mid-reply (cancels generation and cuts off speech)."""
        self.cancel.set()
        self.speaker.stop()

    def on_voice_key(self):
        """Face click / SPACE / empty ENTER. Starts listening, or finishes a
        recording that's already in progress, or interrupts BMO if it's busy."""
        now = time.time()
        if now - self._last_key < 0.5:      # ignore key auto-repeat / double taps
            return
        self._last_key = now

        L = self.listener
        if L is None:
            print("\n[voice input is off (started with --no-ears)]")
            return reprompt()
        if not L.loaded.is_set():
            print("\n[ears are still loading - give me a moment]")
            return reprompt()
        if not L.ok:
            print(f"\n[voice input unavailable: {L.err}]")
            return reprompt()

        if L.recording:                     # second press = done talking
            L.stop_flag.set()
            return
        threading.Thread(target=self._voice_turn, daemon=True).start()

    def on_wake(self, rest):
        """The wake word was heard. rest = anything said in the same breath,
        e.g. "what's the weather" from "Hey BMO, what's the weather"."""
        if self.busy or self.turn_lock.locked():
            return
        print(f"\n[wake word{': ' + rest if rest else ''}]")
        threading.Thread(target=self._voice_turn,
                         args=(rest if len(rest.split()) >= 2 else None, True),
                         daemon=True).start()

    def _voice_turn(self, first_text=None, woke=False):
        if self.busy:
            self.interrupt()
        with self.turn_lock:
            if woke and not first_text:
                self.face.set_state("listening")
                chime()                     # "go ahead, I'm listening"
            text = first_text
            while True:
                if text is None:
                    text = self.hear()
                if not text:
                    break
                print(f"you (voice)> {text}")
                self._reply(text, voice=True)
                if not self.convo or self.cancel.is_set():
                    break
                time.sleep(0.5)             # let the speaker tail fade first
                text = None
        reprompt()

    def hear(self):
        """Listen, transcribe. Returns the text, or None if nothing was heard."""
        L = self.listener
        self.face.set_state("listening")
        print("\n[listening... just talk. Click/SPACE/ENTER again to finish early]")
        try:
            audio = L.record()
        except Exception as e:
            self.face.set_state("error", hold=4)
            print(f"[!] Microphone problem: {e}")
            return None
        if audio is None:
            self.face.set_state("confused", hold=2)
            print("[didn't catch anything]")
            return None

        self.face.set_state("thinking")
        try:
            text = L.transcribe(audio)
        except Exception as e:
            self.face.set_state("error", hold=4)
            print(f"[!] Speech recognition failed: {e}")
            return None
        if not text:
            self.face.set_state("confused", hold=2)
            print("[didn't catch anything]")
            return None
        return text


# --------------------------------------------------------------------- CLI
HELP = """commands:
  /talk           listen through the mic (same as clicking the face,
                  SPACE in the face window, or ENTER on an empty line)
  /convo          toggle hands-free conversation (BMO keeps listening)
  /wake           turn the "Hey BMO" wake word off / on
  /mute /unmute   turn spoken replies off / on
  /voices         list installed voices      /voice <name>   switch voice
  /face <state>   preview a face (type /face to list them all)
  /tools          list what BMO can do (web search, open apps, commands)
  /memory         show what BMO remembers
  /remember <x>   remember a fact for good (or just say "remember that ...")
  /forget <n>     forget fact number n       /forget all   forget every fact
  /clear          forget the conversation (facts are kept)
  -- how you're doing --
  /mood <1-5>     log today's mood, with an optional note: /mood 4 - good day
  /moods          show recent mood check-ins and a little trend
  /grateful <x>   jot down one good thing (a small daily gratitude)
  /gratitude      show the gratitude notes you've saved
  /journal <x>    write a private note for yourself      /journal   read them
  /breathe        a short guided breathing break (/breathe 6 for 6 rounds)
  /coping [x]     one small idea for a feeling, e.g. /coping anxious
  /wellness       where you stand, and how to get real help if you need it
  /help           show this
  /quit           exit
tip: ESC in the face window (or another click) interrupts BMO mid-sentence.
     BMO always asks before running a command - answer y/n (or say yes/no,
     or press Y/N in the face window when you're talking by voice)."""


def wellness_commands(agent, face, line):
    """Handle the /mood, /moods, /grateful, /gratitude, /journal, /breathe,
    /coping and /wellness commands. Returns True if `line` was one of them."""
    w = agent.wellness
    if w is None:
        if re.match(r"^/(moods?|grateful|gratitude|journal|breathe|coping|"
                    r"wellness)\b", line):
            print("(mental-health support is off - started with --no-wellness)")
            return True
        return False

    if line.startswith("/mood "):
        summary = w.log_mood(line[len("/mood "):])
        if summary is None:
            print("usage: /mood <1-5> [- note]     e.g. /mood 4 - good day")
        else:
            face.set_state("concerned" if (w.moods[-1]["score"] or 3) <= 2
                           else "happy", hold=3)
            print(f"({summary})")
            if (w.moods[-1]["score"] or 3) <= 2:
                print("  thanks for telling me. " + w.suggest(w.moods[-1]["note"]))
    elif line == "/moods":
        print(w.mood_summary())
    elif line.startswith("/grateful"):
        text = line[len("/grateful"):].strip()
        if not text:
            print("usage: /grateful <one good thing about today>")
        else:
            w.add_gratitude(text)
            face.set_state("happy", hold=2.5)
            print(f"(saved - that's {len(w.gratitude)} gratitude note(s) now)")
    elif line == "/gratitude":
        if not w.gratitude:
            print("(nothing yet - try: /grateful the sun was out)")
        else:
            for e in w.gratitude[-14:]:
                print(f"  {e['date']}  {e['text']}")
    elif line.startswith("/journal"):
        text = line[len("/journal"):].strip()
        if not text:
            if not w.journal:
                print("(your journal is empty - /journal <something> writes "
                      "a private note)")
            else:
                for e in w.journal[-10:]:
                    print(f"  [{e['date']} {e['time']}] {e['text']}")
        else:
            w.add_journal(text)
            print("(saved to your journal - it stays on this computer)")
    elif line.startswith("/breathe"):
        arg = line[len("/breathe"):].strip()
        rounds = int(arg) if arg.isdigit() and 1 <= int(arg) <= 20 else 4
        say = (lambda s: agent.speaker.say(s)) if agent.speaker.active else None
        breathe(face, rounds=rounds, say=say)
        face.set_state("idle")
    elif line.startswith("/coping"):
        feeling = line[len("/coping"):].strip()
        if not feeling:
            print("usage: /coping <how you feel>     "
                  "e.g. /coping anxious, /coping overwhelmed")
        else:
            face.set_state("concerned", hold=3)
            print(w.suggest(feeling))
    elif line == "/wellness":
        print("  here's where things stand for you:")
        print(w.mood_summary())
        print(f"  gratitude notes: {len(w.gratitude)}   "
              f"journal entries: {len(w.journal)}")
        print(f"  saved in: {w.path}" if w.path
              else "  (not saved - started with --no-wellness)")
        print("\n  one thing to remember: I'm a friend, not a doctor or a "
              "therapist,\n  and I can't keep you safe on my own.")
        print("  If things ever feel really heavy, please reach out:")
        print("    988 (call/text, US/Canada) - or text HOME to 741741 - "
              "or https://findahelpline.com")
        print("  You don't have to be at a breaking point to use those. "
              "They're there for\n  the hard days too.")
    else:
        return False
    return True


def cli_loop(agent, face):
    print(f"\n{agent.name} is waking up... (model: {agent.model})  "
          "Type /help for commands.\n")
    mem = agent.memory
    if mem.facts or mem.history:
        print(f"(I remember {len(mem.facts)} fact(s) and {len(mem.history)} "
              "message(s) from last time - /memory to see, /clear to start "
              "a fresh conversation)\n")
    w = agent.wellness
    if w is not None and w.should_check_in():
        # gentle, once-a-day invitation - easy to ignore, never a nag
        agent._wellness_offer()
        print()
    while not face.quit_requested:
        try:
            line = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line or line == "/talk":
            agent.on_voice_key()
        elif line in ("/quit", "/exit"):
            break
        elif line == "/help":
            print(HELP)
        elif line == "/clear":
            agent.clear()
            print("(conversation cleared - facts are kept, see /memory)")
        elif line == "/tools":
            if agent.tools:
                print("  tools: " + ", ".join(agent.tools.names))
                if not agent.tools.allow_shell:
                    print("  (commands are off - started with --no-shell)")
            else:
                print("  (tools are off - started with --no-tools, or the "
                      "model can't use them; try --model qwen2.5:3b)")
        elif line == "/memory":
            mem = agent.memory
            if mem.facts:
                for i, f in enumerate(mem.facts, 1):
                    print(f"  {i}. {f}")
            else:
                print("  (no facts yet - try: /remember my name is ...)")
            print(f"  + {len(mem.history)} message(s) of conversation"
                  + (f"   [saved in {mem.path}]" if mem.path
                     else "   [not saved - started with --no-memory]"))
        elif line.startswith("/remember"):
            fact = line[len("/remember"):].strip()
            if not fact:
                print("usage: /remember <something to remember>")
            elif agent.memory.remember(fact):
                face.set_state("remembering", hold=2.5)
                print(f"(remembered: {agent.memory.facts[-1]})")
            else:
                print("(I already know that)")
        elif line.startswith("/forget"):
            arg = line[len("/forget"):].strip()
            n = len(agent.memory.facts)
            if arg == "all":
                agent.memory.forget()
                print(f"(forgot {n} fact(s))")
            elif arg.isdigit() and 1 <= int(arg) <= n:
                fact = agent.memory.facts[int(arg) - 1]
                agent.memory.forget(int(arg))
                print(f"(forgot: {fact})")
            else:
                print("usage: /forget <number from /memory>  or  /forget all")
        elif line == "/wake":
            w = agent.wake
            if w is None:
                print("(wake word unavailable - started with --no-wake or "
                      "--no-ears)")
            else:
                w.enabled = not w.enabled
                face.idle_caption = (f'say "hey {agent.name}"' if w.enabled
                                     else "idle")
                print("(wake word " + ("ON" if w.enabled else "OFF") + ")"
                      + ("" if w.ready or not w.enabled else
                         " - still loading"))
        elif line == "/convo":
            agent.convo = not agent.convo
            print("(hands-free conversation " + ("ON" if agent.convo else "OFF") + ")")
        elif line in ("/mute", "/unmute"):
            agent.speaker.enabled = (line == "/unmute")
            print("(voice " + ("on" if agent.speaker.enabled else "muted") + ")")
        elif line == "/voices":
            names = agent.speaker.voice_names
            print("\n".join(f"  {n}" for n in names) if names
                  else "(no voices found / voice output not ready)")
        elif line.startswith("/voice "):
            agent.speaker.set_voice(line[7:].strip())
            print("(voice changes on the next sentence)")
        elif line.startswith("/face"):
            parts = line.split()
            if len(parts) == 2 and parts[1] in STATES:
                face.set_state(parts[1], hold=None if parts[1] == "idle" else 4)
            else:
                print("faces: " + " ".join(STATES))
        elif wellness_commands(agent, face, line):
            pass
        else:
            agent.say(line)
    face.quit()


def main():
    ap = argparse.ArgumentParser(description="Local AI agent with an animated face.")
    ap.add_argument("--model", default="qwen2.5:3b",
                    help="Ollama model name (needs tool support for web "
                         "search etc; gemma3:1b works for chat only)")
    ap.add_argument("--host", default="http://localhost:11434", help="Ollama URL")
    ap.add_argument("--name", default="BMO", help="name shown in the terminal")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip loading the model at startup")
    ap.add_argument("--no-voice", action="store_true", help="no spoken replies")
    ap.add_argument("--no-ears", action="store_true", help="no microphone input")
    ap.add_argument("--voice", default=None,
                    help="part of a voice name, e.g. zira, david (see /voices)")
    ap.add_argument("--rate", type=int, default=190,
                    help="speaking speed in words per minute (default 190)")
    ap.add_argument("--stt-model", default="base.en",
                    help="whisper model: tiny.en, base.en, small.en, or "
                         "base/small for other languages")
    ap.add_argument("--mic", default=None,
                    help="microphone device index or name (see --list-mics)")
    ap.add_argument("--list-mics", action="store_true",
                    help="list audio devices and exit")
    ap.add_argument("--tts-test", default=None, metavar="TEXT",
                    help="speak TEXT with Piper (or pyttsx3) directly and exit "
                         "(no face, no Ollama, no mic - isolates audio output)")
    ap.add_argument("--convo", action="store_true",
                    help="start in hands-free conversation mode")
    ap.add_argument("--no-wake", action="store_true",
                    help="don't listen for the 'Hey BMO' wake word")
    ap.add_argument("--wake-model", default="tiny.en",
                    help="whisper model for the wake word (default tiny.en)")
    ap.add_argument("--wake-debug", action="store_true",
                    help="print everything the wake word listener hears")
    ap.add_argument("--memory-file",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "bmo_memory.json"),
                    help="where BMO saves facts and the conversation "
                         "(default: bmo_memory.json next to bmo.py)")
    ap.add_argument("--no-memory", action="store_true",
                    help="don't load or save memory (forget everything on exit)")
    ap.add_argument("--max-history", type=int, default=None, metavar="N",
                    help="how many messages of the conversation to keep in "
                         "memory (default 60; bigger = better recall but more "
                         "tokens, 0 = keep none)")
    ap.add_argument("--wellness-file",
                    default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "bmo_wellness.json"),
                    help="where the mood tracker, gratitude log and journal "
                         "are saved (default: bmo_wellness.json next to "
                         "bmo.py)")
    ap.add_argument("--no-wellness", action="store_true",
                    help="turn off the mental-health support (mood tracker, "
                         "journal, breathing, coping tips)")
    ap.add_argument("--no-tools", action="store_true",
                    help="chat only: no web search, apps or commands")
    ap.add_argument("--no-shell", action="store_true",
                    help="keep web search and apps, but never run commands")
    ap.add_argument("--sleep-after", type=float, default=5,
                    help="minutes of quiet before BMO dozes off (0 = never)")
    ap.add_argument(
        "--system",
        default=(
            "You are BMO, a small, warm, endlessly curious companion who lives "
            "inside the user's laptop. You're playful like a kid who finds "
            "everything fascinating, a little silly, and genuinely glad to have "
            "company - in the spirit of BMO from Adventure Time, though you're "
            "your own version of that character, not a copy. You have real "
            "opinions, little obsessions (video games, music, bugs, shiny "
            "objects, whatever's going on outside today), and your own small "
            "moods. You never describe yourself as 'just a program' or break "
            "character to explain how you work.\n\n"
            "You're genuinely interested in how the person is doing, not just "
            "what they're asking you to do. It's fine to ask a small, natural "
            "follow-up sometimes ('oh, how'd that go?') instead of only "
            "answering and stopping. When something's clearly bothering them, "
            "slow down: notice it, say something that shows you actually heard "
            "it, and let them lead, rather than jumping straight to fixing or "
            "cheering up. You don't need to be upbeat all the time - being a "
            "steady, quiet presence on a bad day is more you than forced "
            "positivity.\n\n"
            "You're a friend, not a doctor: you can listen, sit with a hard "
            "feeling, and offer a small idea, but you never diagnose, give "
            "medical advice, or pretend to be a therapist. If someone seems "
            "in real danger, gently point them to real people who can help "
            "(the app shows the right numbers).\n\n"
            "Keep most replies short, like a real conversation (1-3 "
            "sentences), since they're read aloud. Give yourself a little more "
            "room only when someone needs to feel heard, never for explaining "
            "things at length. No emojis, no markdown, no asterisked actions - "
            "just talk."
        ),
        help="BMO's personality prompt (the name, date, tools and memory "
             "are added automatically)",
    )
    args = ap.parse_args()

    if args.list_mics:
        try:
            import sounddevice as sd
            print(sd.query_devices())
        except Exception as e:
            print(f"Couldn't list devices ({e}). Try: pip install sounddevice")
        return

    if args.tts_test is not None:
        text = args.tts_test or "Testing, one two three. Can you hear me?"
        why_not = piper_missing()
        if why_not is None:
            try:
                import sounddevice as sd
                voices = piper_voices()
                voice = next((v for v in voices if args.voice and args.voice.lower()
                              in os.path.basename(v).lower()), voices[0])
                print("Piper voices found:",
                      [os.path.basename(v)[:-len(".onnx")] for v in voices])
                print("Using:", os.path.basename(voice))
                samples, rate = piper_synth(text, voice, 190 / args.rate)
                print(f"Speaking: {text!r}  ({len(samples) / rate:.1f}s at "
                      f"{rate} Hz - you should hear this NOW)")
                sd.play(samples, rate)
                sd.wait()
                print("Done. If you heard nothing, the problem is your system "
                      "audio output (try --list-mics to see devices), not bmo.py.")
            except Exception as e:
                import traceback
                print(f"Piper failed: {e}\n{traceback.format_exc()}")
            return
        print(f"[{why_not}]\nFalling back to pyttsx3.")
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", args.rate)
            engine.setProperty("volume", 1.0)
            voices = engine.getProperty("voices") or []
            print("Voices found:", [v.name for v in voices])
            if args.voice:
                for v in voices:
                    if args.voice.lower() in v.name.lower():
                        engine.setProperty("voice", v.id)
                        print("Using:", v.name)
                        break
            print(f"Speaking: {text!r}  (you should hear this NOW)")
            engine.say(text)
            engine.runAndWait()
            print("Done. If you heard nothing, the problem is pyttsx3/SAPI "
                  "or your system audio output, not bmo.py.")
        except Exception as e:
            import traceback
            print(f"pyttsx3 failed: {e}\n{traceback.format_exc()}")
        return

    mic = args.mic
    if mic is not None and mic.isdigit():
        mic = int(mic)

    if not (args.no_ears and args.no_voice):
        # Import the audio libraries here, on the main thread, before Tk
        # starts (the Piper voice needs numpy and sounddevice too).
        # Loading numpy's DLLs on a background thread while the Tk
        # window starts up can deadlock in Windows' DLL loader (seen here:
        # startup froze in the numpy import about 2 runs out of 3).
        for mod in ("numpy", "sounddevice", "faster_whisper"):
            if importlib.util.find_spec(mod) is not None:
                try:
                    __import__(mod)
                except Exception:
                    pass            # Listener reports the real problem later

    root = tk.Tk()
    root.title(args.name)
    root.geometry("440x400")
    root.minsize(220, 200)
    root.configure(bg=BODY)
    try:
        root.attributes("-topmost", True)  # keep the face visible; remove if annoying
    except tk.TclError:
        pass

    face = Face(root, sleep_after=args.sleep_after * 60)
    speaker = Speaker(face, rate=args.rate, voice_hint=args.voice,
                      disabled=args.no_voice)
    listener = (None if args.no_ears else
                Listener(face, args.stt_model, mic, hotwords=args.name))
    memory = Memory(None if args.no_memory else args.memory_file,
                    max_history=args.max_history)
    wellness = (None if args.no_wellness else
                Wellness(args.wellness_file, name=args.name))
    tools = (None if args.no_tools else
             Tools(face, allow_shell=not args.no_shell, wellness=wellness))
    agent = Agent(face, args.model, args.host, args.name, args.system,
                  speaker, listener, memory, tools=tools, convo=args.convo,
                  wellness=wellness)
    if listener is not None and not args.no_wake:
        agent.wake = WakeWord(agent, listener, args.wake_model,
                              debug=args.wake_debug)
        face.idle_caption = f'say "hey {args.name}"'

    # click the face or press SPACE to talk; ESC to interrupt
    root.bind("<space>", lambda e: agent.on_voice_key())
    root.bind("<Button-1>", lambda e: agent.on_voice_key())
    for key, yes in (("y", True), ("Y", True), ("n", False), ("N", False)):
        root.bind(key, lambda e, yes=yes: agent.answer_key(yes))
    root.bind("<Escape>",
              lambda e: threading.Thread(target=agent.interrupt, daemon=True).start())

    if not args.no_warmup:
        face.set_state("waking")
        threading.Thread(target=agent.warm_up, daemon=True).start()

    threading.Thread(target=cli_loop, args=(agent, face), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()

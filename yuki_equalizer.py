#!/usr/bin/env python3
"""Korektor Yuki – wersja na Linuksa (Mint/XFCE, PipeWire). Wygląd 1:1 z aplikacją Fire TV (uk.goblinstudio.yukieq 1.3.2).
Cały ekran jest rysowany tak jak na telewizorze: wirtualne płótno 1920×1080 skalowane do okna."""
import ctypes, math, os, subprocess, sys, threading, time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


def _load_fonts():
    """Roboto (font Fire TV / Androida) ładowany tylko dla tej aplikacji – nic nie jest instalowane w systemie."""
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cfg = fc.FcConfigGetCurrent()
        for f in ("Roboto-Regular.ttf", "Roboto-Medium.ttf", "Roboto-Bold.ttf"):
            fc.FcConfigAppFontAddFile(cfg, os.path.join(APP_DIR, "fonts", f).encode())
    except Exception as e:
        print("Roboto:", e, file=sys.stderr)


_load_fonts()

import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0"); gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0"); gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo, GdkPixbuf, Gio
import cairo
import numpy as np

from yuki_state import State, T, FREQS, NAMES, BG_COUNT, NAME_MAX, CUSTOM_BG
import yuki_audio

VW, VH = 1920.0, 1080.0
FONT = "Roboto"
W_REG, W_MED, W_BOLD = Pango.Weight.NORMAL, Pango.Weight.MEDIUM, Pango.Weight.BOLD


def C(r, g, b, a=255):
    return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)


CYAN, PINK, ORANGE, GREEN, RED = C(64, 200, 255), C(255, 90, 170), C(255, 150, 40), C(40, 190, 110), C(200, 50, 60)
YELLOW = C(255, 200, 40)
BTN_BG = C(32, 30, 48, 200)
TOP_K, BOT_K = 1.0562, 0.2710   # Roboto FontMetrics.top / bottom (Android includeFontPadding)


def rrect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0); cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi); cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


class Txt:
    """Tekst przez Pango z metryką jak TextView/Paint na Androidzie."""

    def __init__(self):
        self.fm = PangoCairo.FontMap.get_default()
        self.ctx = self.fm.create_context()
        fo = cairo.FontOptions(); fo.set_hint_style(cairo.HINT_STYLE_NONE); fo.set_hint_metrics(cairo.HINT_METRICS_OFF)
        fo.set_antialias(cairo.ANTIALIAS_GRAY)
        PangoCairo.context_set_font_options(self.ctx, fo)
        self.cache = {}; self.lang = None

    def set_lang(self, l):
        if l != self.lang:
            self.lang = l; self.cache.clear()
            self.ctx.set_language(Pango.Language.from_string({"ja": "ja", "en": "en"}.get(l, "pl")))

    def layout(self, s, size, weight=W_REG, width=None, ellipsize=False, align=Pango.Alignment.LEFT):
        key = (s, size, weight, width, ellipsize, align)
        lo = self.cache.get(key)
        if lo is None:
            if len(self.cache) > 600:
                self.cache.clear()
            lo = Pango.Layout.new(self.ctx)
            fd = Pango.FontDescription(); fd.set_family(FONT); fd.set_weight(weight)
            fd.set_absolute_size(size * Pango.SCALE)
            lo.set_font_description(fd); lo.set_text(s, -1); lo.set_alignment(align)
            if width:
                lo.set_width(int(width * Pango.SCALE))
                if ellipsize:
                    lo.set_ellipsize(Pango.EllipsizeMode.END)
                else:
                    lo.set_wrap(Pango.WrapMode.WORD_CHAR)
            self.cache[key] = lo
        return lo

    def width(self, s, size, weight=W_REG):
        return self.layout(s, size, weight).get_extents()[1].width / Pango.SCALE

    def draw(self, cr, s, x, y, size, col, weight=W_REG, mode="top", align="left", width=None, ellipsize=False,
             fake_bold=False, spans=None):
        """mode: top = y to górna krawędź TextView; base = y to linia bazowa; center = y to środek przycisku."""
        if not s:
            return
        al = {"left": Pango.Alignment.LEFT, "center": Pango.Alignment.CENTER, "right": Pango.Alignment.RIGHT}[align]
        lo = self.layout(s, size, weight, width, ellipsize, al if width else Pango.Alignment.LEFT)
        PangoCairo.update_context(cr, self.ctx); lo.context_changed()
        if spans:
            attrs = Pango.AttrList()
            for (a, b, c) in spans:
                at = Pango.attr_foreground_new(int(c[0] * 65535), int(c[1] * 65535), int(c[2] * 65535))
                at.start_index, at.end_index = a, b; attrs.insert(at)
            lo.set_attributes(attrs)
        else:
            lo.set_attributes(None)
        bl = lo.get_baseline() / Pango.SCALE
        if mode == "top":
            ty = y + TOP_K * size - bl
        elif mode == "center":
            ty = y + (TOP_K - (TOP_K + BOT_K) / 2) * size - bl
        else:
            ty = y - bl
        lw = lo.get_extents()[1].width / Pango.SCALE
        if width:
            tx = x
        elif align == "center":
            tx = x - lw / 2
        elif align == "right":
            tx = x - lw
        else:
            tx = x
        cr.save(); cr.move_to(tx, ty); cr.set_source_rgba(*col); PangoCairo.show_layout(cr, lo)
        if fake_bold:
            cr.move_to(tx, ty); PangoCairo.layout_path(cr, lo); cr.set_line_width(size / 36.0); cr.stroke()
        cr.restore()


class Item:
    def __init__(self, iid, x, y, w, h):
        self.id, self.x, self.y, self.w, self.h = iid, x, y, w, h
        self.anim = 0.0  # 0..1 animacja fokusu

    def hit(self, px, py):
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


class Button(Item):
    def __init__(self, iid, x, y, w, h, size):
        super().__init__(iid, x, y, w, h); self.size = size; self.text = ""; self.color = C(220, 220, 235)


class Slider(Item):
    def __init__(self, iid, x, y, w, h, label, vmin, vmax, step, accent, percent=False):
        super().__init__(iid, x, y, w, h)
        self.label, self.min, self.max, self.step, self.accent, self.percent = label, vmin, vmax, step, accent, percent
        self.value = vmin if percent else 0.0

    def set(self, v):
        self.value = max(self.min, min(self.max, v))

    def fmt(self):
        if self.percent:
            return "%d%%" % int(self.value)
        v = int(round(self.value))
        return ("+" if v > 0 else "") + str(v) + " dB"

    def track(self):
        nl = len(self.label.split("\n"))
        return 70.0, self.h - (96.0 if nl > 1 else 70.0)

    def value_at(self, vy):
        top, bot = self.track()
        cy = self.y + self.h / 2; vy = cy + (vy - cy) / (1 + 0.04 * self.anim)  # suwak z fokusem jest powiększony o 4%
        f = (bot - (vy - self.y)) / (bot - top)
        v = self.min + max(0.0, min(1.0, f)) * (self.max - self.min)
        return round(v / self.step) * self.step


class Viz:
    """Wizualizer LED (20 słupków) – jak VizView.java; dźwięk z monitora domyślnego wyjścia (tylko analiza w pamięci)."""
    BARS = 20; F_LO = 45.0; F_HI = 15000.0; CAP = 1024; SR = 44100.0

    def __init__(self):
        self.lvl = [0.0] * self.BARS; self.peak = [0.0] * self.BARS; self.hold = [0.0] * self.BARS
        self.curve = [0.0] * self.BARS; self.proc = None; self.frame = None; self.running = False
        self.last = time.monotonic(); self.quiet_since = self.last; self.monitor = None
        nb = self.CAP // 2; self.lo = []; self.hi = []
        for i in range(self.BARS):
            f0 = self.F_LO * (self.F_HI / self.F_LO) ** (i / self.BARS); f1 = self.F_LO * (self.F_HI / self.F_LO) ** ((i + 1) / self.BARS)
            a = int(math.floor(f0 * self.CAP / self.SR)); b = int(math.ceil(f1 * self.CAP / self.SR))
            a = max(1, min(nb - 1, a)); b = max(a + 1, min(nb, b)); self.lo.append(a); self.hi.append(b)

    def set_curve(self, st):
        for i in range(self.BARS):
            db = 0.0
            if st.master:
                fc = self.F_LO * (self.F_HI / self.F_LO) ** ((i + 0.5) / self.BARS)
                b = 9
                for k in range(10):
                    if fc <= yuki_audio.cutoff(k):
                        b = k; break
                db = st.gains[b] + (st.bass if fc <= 90 else st.bass * 0.5 if fc <= 180 else 0.0)
            self.curve[i] = db

    def start(self):
        self.stop()
        try:
            self.monitor = yuki_audio.default_sink()
            self.proc = subprocess.Popen(["parec", "-d", "@DEFAULT_MONITOR@", "--raw", "--format=s16le", "--rate=44100",
                                          "--channels=1", "--latency-msec=15", "--client-name=Korektor Yuki",
                                          "--stream-name=Wizualizer (analiza)"],
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except Exception:
            self.proc = None; return False
        self.running = True; self.last = time.monotonic(); self.quiet_since = self.last
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        return True

    def _reader(self, proc):
        """Czyta wszystko, co już przyszło (bez kolejki zaległości) i trzyma ostatnie 1024 próbki = najświeższy dźwięk."""
        fd = proc.stdout.fileno(); buf = np.zeros(self.CAP, np.float32); rest = b""
        while proc.poll() is None:
            try:
                b = os.read(fd, 65536)
            except OSError:
                break
            if not b:
                break
            b = rest + b; n = len(b) & ~1; rest = b[n:]
            x = np.frombuffer(b[:n], dtype="<i2").astype(np.float32) / 32768.0
            buf = x[-self.CAP:].copy() if len(x) >= self.CAP else np.concatenate((buf[len(x):], x))
            self.frame = buf; self.frame_t = time.monotonic()

    def stop(self):
        self.running = False
        if self.proc:
            try:
                self.proc.terminate(); self.proc.wait(1)
            except Exception:
                try: self.proc.kill()
                except Exception: pass
        self.proc = None; self.frame = None
        for i in range(self.BARS):
            self.lvl[i] = 0.0; self.peak[i] = 0.0

    def sample(self, dt):
        fr = self.frame; mags = None
        if fr is not None:
            pk = float(np.max(np.abs(fr)))
            x = fr / pk if pk > 1e-3 else fr          # SCALING_MODE_NORMALIZED jak na Fire TV
            X = np.fft.rfft(x) / self.CAP * 2.0 * 128.0  # skala jak 8-bitowe FFT Visualizera
            mags = (X.real ** 2 + X.imag ** 2)
        attack = 1 - 0.0005 ** dt; release = 1 - 0.08 ** dt
        for i in range(self.BARS):
            target = 0.0
            if mags is not None:
                m = float(np.max(mags[self.lo[i]:self.hi[i]]))
                db = 10 * math.log10(m + 1e-3) + 6.0 * i / (self.BARS - 1) + self.curve[i]
                target = max(0.0, min(1.0, (db - 12) / 34))
            self.lvl[i] += (target - self.lvl[i]) * (attack if target > self.lvl[i] else release)
            if self.lvl[i] >= self.peak[i]:
                self.peak[i] = self.lvl[i]; self.hold[i] = 0.45
            elif self.hold[i] > 0:
                self.hold[i] -= dt
            else:
                self.peak[i] = max(self.lvl[i], self.peak[i] - 0.35 * dt)

    def draw(self, cr, x0, y0, w, h):
        now = time.monotonic(); dt = min(0.1, now - self.last); self.last = now
        if self.running:
            self.sample(dt)
        g = cairo.LinearGradient(0, y0, 0, y0 + h); g.add_color_stop_rgba(0, *C(8, 6, 14, 0)); g.add_color_stop_rgba(1, *C(8, 6, 14, 150))
        cr.rectangle(x0, y0, w, h); cr.set_source(g); cr.fill()
        pad = 14.0; slot = (w - 2 * pad) / self.BARS; bw = slot * 0.66
        segH, segGap = 6.0, 3.0; step = segH + segGap; nseg = int((h - 6) / step); anyl = False
        for i in range(self.BARS):
            x = x0 + pad + i * slot + (slot - bw) / 2; lit = round(self.lvl[i] * nseg); pk = round(self.peak[i] * nseg)
            if self.lvl[i] > 0.01 or self.peak[i] > 0.01:
                anyl = True
            for s in range(nseg):
                f = s / float(nseg - 1); yb = y0 + h - 4 - s * step
                col = (40, 220, 90) if f < 0.5 else (230, 225, 50) if f < 0.7 else (255, 150, 30) if f < 0.86 else (240, 50, 50)
                a = int(230 - 60 * f) if s < lit else 20
                if s == pk - 1 and pk > lit and pk > 0:
                    a = 255
                cr.set_source_rgba(col[0] / 255, col[1] / 255, col[2] / 255, a / 255); cr.rectangle(x, yb - segH, bw, segH); cr.fill()
        if anyl:
            self.quiet_since = now
        return (now - self.quiet_since) < 1.5


class App(Gtk.Window):
    def __init__(self):
        super().__init__(title="Korektor Yuki")
        self.st = State.load(); T.st = self.st; T.set_lang(self.st.lang)
        self.txt = Txt(); self.txt.set_lang(T.lang)
        self.engine = yuki_audio.Engine(); self.viz = Viz(); self.viz.set_curve(self.st)
        try:
            self.set_icon_from_file(os.path.join(APP_DIR, "assets", "ikona.png"))
        except Exception:
            pass
        self.bgs = {i: cairo.ImageSurface.create_from_png(os.path.join(APP_DIR, "assets", "yuki_bg%d.png" % i)) for i in range(1, BG_COUNT + 1)}
        self.custom = None; self.load_custom()
        self.bg_alpha = 1.0; self.bg_fade = None
        self.status_key = "off"; self.bt_text = None
        self.toast = None; self.dialog = None
        self.focus = "master"; self.key_mode = False; self.press = None; self.drag = None; self.ok_down = None
        self._build()
        self.set_title(T.app())
        self.set_default_size(1280, 720)
        geo = Gdk.Geometry(); geo.min_aspect = geo.max_aspect = VW / VH; geo.min_width, geo.min_height = 640, 360
        self.set_geometry_hints(None, geo, Gdk.WindowHints.ASPECT | Gdk.WindowHints.MIN_SIZE)
        self.da = Gtk.DrawingArea(); self.da.set_can_focus(True); self.add(self.da)
        self.da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK
                           | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.KEY_RELEASE_MASK)
        self.da.connect("draw", self.on_draw)
        self.da.connect("button-press-event", self.on_press); self.da.connect("button-release-event", self.on_release)
        self.da.connect("motion-notify-event", self.on_motion); self.da.connect("scroll-event", self.on_scroll)
        self.connect("key-press-event", self.on_key); self.connect("key-release-event", self.on_key_up)
        self.connect("window-state-event", self.on_wstate); self.connect("destroy", self.on_quit)
        self.anim_on = False; self.viz_tick = None
        self.sync_sliders(); self.refresh_ui()
        if os.environ.get("YUKI_OFFSCREEN"):
            return
        if self.st.master:
            self.engine.start(self.st, save_cb=self.st.save)
        GLib.timeout_add(800, self.status_tick); self.status_tick()
        GLib.timeout_add_seconds(5, self.bt_tick); GLib.idle_add(lambda: self.bt_tick() and False)  # jednorazowo (idle zwracające True powtarzało się bez końca)
        self.start_viz()

    # ---------- budowa układu (współrzędne z MainActivity.java, ekran 1920×1080) ----------
    def _build(self):
        cx, cw = 610.0, 300.0
        self.master = Button("master", cx, 40, cw, 110, 34)
        self.items = [self.master]; self.presets = []
        y = 172.0
        for i in range(5):
            b = Button("p%d" % i, cx, y, cw, 64, 26); b.tag = NAMES[i]; self.presets.append(b); y += 72
        for k in range(1, 4):
            b = Button("u%d" % k, cx, y, cw, 64, 26); b.tag = "Moje %d" % k; self.presets.append(b); y += 72
        self.items += self.presets
        self.save_b = Button("save", cx, y + 8, cw, 64, 24); y += 80
        self.lang_b = Button("lang", cx, y + 8, cw, 60, 22); y += 68
        self.bg_b = Button("bg", cx, y + 8, cw, 60, 22); y += 68
        self.viz_b = Button("viz", cx, y + 8, cw, 60, 22)
        self.items += [self.save_b, self.lang_b, self.bg_b, self.viz_b]
        self.left_col = [b.id for b in self.items]
        sx = 950.0; self.sx = sx
        colW, gx, top, hgt = 72.0, sx, 170.0, 690.0
        self.bands = []
        for i, f in enumerate(FREQS):
            lab = ("%dk" % (f // 1000)) if f >= 1000 else str(f)
            s = Slider("b%d" % i, gx, top, colW - 4, hgt, lab, -12, 12, 1, CYAN); self.bands.append(s); gx += colW
        gx += 14
        self.bass = Slider("bass", gx, top, colW + 4, hgt, "", 0, 12, 1, PINK); gx += colW + 12
        self.boost = Slider("boost", gx, top, 116, hgt, "", 100, 200, 10, ORANGE, percent=True)
        self.sliders = self.bands + [self.bass, self.boost]
        self.slider_ids = [s.id for s in self.sliders]
        self.all = {it.id: it for it in self.items + self.sliders}
        self.art_btns()

    # ---------- teksty ----------
    def status_text(self):
        s = self.st
        if not s.master:
            return T.t("Wyłączony – dźwięk bez zmian", "Off – sound unchanged", "オフ – 音は変更されません")
        if self.status_key in ("off", "starting"):
            return T.t("Uruchamianie…", "Starting…", "起動中…")
        eng = T.t("aktywny ✓", "active ✓", "動作中 ✓") if self.status_key == "active" else T.t("podpinanie…", "attaching…", "接続中…")
        return T.t("Włączony", "On", "オン") + " · " + T.preset(s.preset) + " · " + T.t("wzmocnienie ", "boost ", "ブースト ") + str(s.boost) + "% · " + eng

    def refresh_ui(self):
        s = self.st
        self.master.text = T.t("●  WŁĄCZONY", "●  ON", "●  オン") if s.master else T.t("○  WYŁĄCZONY", "○  OFF", "○  オフ")
        for b in self.presets:
            if b.tag.startswith("Moje"):
                k = int(b.tag[-1]); b.text = T.preset(b.tag) + ("" if s.has_user(k) else T.t(" (puste)", " (empty)", " (空)"))
            else:
                b.text = T.preset(b.tag)
        self.save_b.text = T.t("💾 Zapisz jako własny", "💾 Save as custom", "💾 マイ設定として保存")
        self.lang_b.text = T.t("🌐 Język: POL  (ENG)", "🌐 Language: ENG  (JPN)", "🌐 言語: 日本語  (POL)")
        if s.bg == BG_COUNT + 1:
            self.bg_b.text = T.t("🖼 Tło: własne  ▸", "🖼 Background: custom  ▸", "🖼 背景: カスタム  ▸")
        else:
            self.bg_b.text = T.t("🖼 Tło: ", "🖼 Background: ", "🖼 背景: ") + "%d / %d  ▸" % (s.bg, BG_COUNT)
        self.viz_b.text = T.t("📊 Wizualizer: WŁ.", "📊 Visualizer: ON", "📊 スペクトル: オン") if s.viz else T.t("📊 Wizualizer: WYŁ.", "📊 Visualizer: OFF", "📊 スペクトル: オフ")
        self.bass.label = T.t("Bas", "Bass", "低音"); self.boost.label = T.t("Wzmoc-\nnienie", "Boost", "ブースト")
        self.redraw()

    def preset_lbl(self):
        return "• " + T.preset(self.st.preset) + (T.t("  (kompresor)", "  (compressor)", "  (コンプレッサー)") if self.st.comp else "")

    def warn_text(self):
        s = self.st
        if s.boost > 100:
            return T.t("⚠ Wzmocnienie %d%% (+%.1f dB). Wysokie wartości mogą zniekształcać dźwięk i szkodzić uszom!",
                       "⚠ Boost %d%% (+%.1f dB). High values can distort the sound and hurt your ears!",
                       "⚠ ブースト%d%% (+%.1f dB)。高い値は音の歪みや耳へのダメージの原因になります!") % (s.boost, s.boost_db())
        return T.t("Wzmocnienie 100% = bez zmian. ⚠ Powyżej 150% możliwe zniekształcenia – uważaj na uszy.",
                   "Boost 100% = no change. ⚠ Above 150% distortion is possible – protect your ears.",
                   "ブースト100% = 変化なし。⚠ 150%を超えると音が歪むことがあります。耳に注意してください。")

    # ---------- rysowanie ----------
    def geom(self):
        a = self.da.get_allocation(); sc = min(a.width / VW, a.height / VH)
        return sc, (a.width - VW * sc) / 2, (a.height - VH * sc) / 2, a.width, a.height

    def redraw(self):
        """Zmiana stanu: przebuduj warstwę interfejsu przy następnym rysowaniu."""
        self.ui_dirty = True
        if hasattr(self, "da"):
            self.da.queue_draw()

    def shown_bg(self):
        bgn = self.st.bg
        if self.bg_fade is not None and time.monotonic() - self.bg_fade < 0.12:
            bgn = getattr(self, "old_bg", self.st.bg)  # najpierw wygaszenie starego obrazu (jak showBg() na Fire TV)
        return bgn

    @staticmethod
    def paint_gradient(cr, w, h):
        g = cairo.LinearGradient(0, 0, w, h)
        g.add_color_stop_rgb(0, 14 / 255, 12 / 255, 24 / 255); g.add_color_stop_rgb(0.5, 22 / 255, 16 / 255, 38 / 255); g.add_color_stop_rgb(1, 10 / 255, 10 / 255, 18 / 255)
        cr.set_source(g); cr.paint()

    def on_draw(self, w, cr):
        """Rysowanie w pamięci (cairo image) i jedno przesłanie na ekran. Tło+obraz i interfejs są w osobnych
        warstwach-buforach; co klatkę wizualizera dorysowywane są tylko słupki (wcześniej cała scena ~57 ms/klatkę)."""
        sc, ox, oy, aw, ah = self.geom()
        if aw < 2 or ah < 2:
            return True
        bgn = self.shown_bg()
        key = (aw, ah, bgn, round(self.bg_alpha, 3), id(self.custom))
        if getattr(self, "_base_key", None) != key:
            self._base = cairo.ImageSurface(cairo.FORMAT_RGB24, aw, ah); b = cairo.Context(self._base)
            self.paint_gradient(b, aw, ah); b.translate(ox, oy); b.scale(sc, sc); b.rectangle(0, 0, VW, VH); b.clip()
            self.draw_base(b, bgn); self._base_key = key
        if getattr(self, "ui_dirty", True) or getattr(self, "_ui_size", None) != (aw, ah):
            self._ui = cairo.ImageSurface(cairo.FORMAT_ARGB32, aw, ah); u = cairo.Context(self._ui)
            u.translate(ox, oy); u.scale(sc, sc); u.rectangle(0, 0, VW, VH); u.clip()
            self.draw_ui(u); self.ui_dirty = False; self._ui_size = (aw, ah)
        if getattr(self, "_frame_size", None) != (aw, ah):
            self._frame = cairo.ImageSurface(cairo.FORMAT_RGB24, aw, ah); self._frame_size = (aw, ah)
        x1, y1, x2, y2 = cr.clip_extents()
        f = cairo.Context(self._frame); f.rectangle(x1, y1, x2 - x1, y2 - y1); f.clip()
        f.set_operator(cairo.OPERATOR_SOURCE); f.set_source_surface(self._base, 0, 0); f.paint(); f.set_operator(cairo.OPERATOR_OVER)
        if self.st.viz and self.viz.running:
            f.save(); f.translate(ox, oy); f.scale(sc, sc); self.viz.draw(f, 0, 820, 608, 210); f.restore()
        f.set_source_surface(self._ui, 0, 0); f.paint()
        cr.set_source_surface(self._frame, 0, 0); cr.paint()
        return True

    def draw_base(self, cr, bgn):
        img = self.custom if (bgn == BG_COUNT + 1 and self.custom) else self.bgs.get(bgn, self.bgs[1])
        cr.save(); cr.set_source_surface(img, 0, 0); cr.get_source().set_filter(cairo.FILTER_GOOD); cr.paint_with_alpha(self.bg_alpha); cr.restore()

    def draw_scene(self, cr):
        """Cała scena naraz (tryb --render)."""
        self.draw_base(cr, self.shown_bg())
        if self.st.viz and self.viz.running:
            self.viz.draw(cr, 0, 820, 608, 210)
        self.draw_ui(cr)

    def draw_ui(self, cr):
        st = self.st; tx = self.txt
        tx.draw(cr, "Goblin Studio", 24, 1036, 20, C(255, 255, 255, 150))
        for b in self.art_btns():
            self.draw_art_btn(cr, b)
        # kolumna przycisków
        for b in self.items:
            self.draw_button(cr, b)
        # nagłówek
        sx = self.sx
        tx.draw(cr, T.app(), sx, 26, 56, C(255, 255, 255), W_BOLD)
        tx.draw(cr, self.preset_lbl(), sx + 500, 52, 30, PINK, W_BOLD, width=450, ellipsize=True)
        tx.draw(cr, self.status_text(), sx, 112, 21, C(150, 220, 170), width=940)
        for s in self.sliders:
            self.draw_slider(cr, s)
        tx.draw(cr, "Hz", sx - 40, 170 + 690 - 48, 20, C(120, 120, 145))
        tx.draw(cr, self.warn_text(), sx, 880, 22, ORANGE, width=940)
        tx.draw(cr, T.t("◀ ▶ wybór suwaka   ▲ ▼ zmiana wartości   Enter na przycisku: wybór   ◀ z pierwszego suwaka: menu",
                        "◀ ▶ select slider   ▲ ▼ change value   Enter on a button: select   ◀ from first slider: menu",
                        "◀ ▶ スライダー選択   ▲ ▼ 値を変更   ボタンでEnter: 決定   最初のスライダーで◀: メニュー"), sx, 960, 20, C(140, 140, 165), width=960)
        tx.draw(cr, T.t("ℹ Po włączeniu dźwięk systemu idzie przez wyjście „Korektor Yuki”. Wyłączenie przywraca poprzednie wyjście.",
                        "ℹ When on, system sound goes through the “Yuki Equalizer” output. Turning off restores the previous output.",
                        "ℹ オンにするとシステム音声は「Yuki Equalizer」出力を通ります。オフで元の出力に戻ります。"), sx, 1004, 19, C(130, 190, 230), width=960)
        tx.draw(cr, T.t("Przytrzymaj Enter lub kliknij prawym na Moje 1–3: zmień nazwę", "Hold Enter or right-click on My 1–3: rename",
                        "マイ1–3でEnter長押しまたは右クリック: 名前を変更"), sx, 1040, 20, C(140, 140, 165), width=960)
        if self.bt_text:
            self.draw_bt(cr)
        if self.dialog:
            self.draw_dialog(cr)
        if self.toast:
            self.draw_toast(cr)

    def focused(self, iid):
        return self.focus == iid and (self.key_mode or iid in self.slider_ids) and not self.dialog

    def draw_button(self, cr, b):
        st = self.st; f = self.focused(b.id); sc = 1 + 0.06 * b.anim
        cr.save(); cx, cy = b.x + b.w / 2, b.y + b.h / 2
        cr.translate(cx, cy); cr.scale(sc, sc); cr.translate(-cx, -cy)
        if b is self.master:
            fill, stroke, tcol = (GREEN if st.master else RED), (C(255, 255, 255) if f else None), C(255, 255, 255)
        elif b in self.presets:
            act = st.preset.replace("*", "") == b.tag
            fill = C(70, 40, 110) if act else BTN_BG
            stroke = CYAN if f else (PINK if act else None)
            tcol = C(255, 255, 255) if f else C(205, 205, 222)
        else:
            fill, stroke, tcol = BTN_BG, (CYAN if f else None), C(220, 220, 235)
        rrect(cr, b.x, b.y, b.w, b.h, 14); cr.set_source_rgba(*fill); cr.fill()
        if stroke:
            rrect(cr, b.x + 2, b.y + 2, b.w - 4, b.h - 4, 12); cr.set_source_rgba(*stroke); cr.set_line_width(4); cr.stroke()
        single = b.id.startswith("u")
        self.txt.draw(cr, b.text, b.x + 10, b.y + b.h / 2, b.size, tcol, W_MED, mode="center", align="center",
                      width=b.w - 20, ellipsize=single)
        cr.restore()

    def draw_slider(self, cr, s):
        f = self.focused(s.id); w, h = s.w, s.h; ac = s.accent
        cr.save(); cx0, cy0 = s.x + w / 2, s.y + h / 2
        sxs, sys_ = 1 + 0.08 * s.anim, 1 + 0.04 * s.anim
        cr.translate(cx0, cy0); cr.scale(sxs, sys_); cr.translate(-cx0, -cy0); cr.translate(s.x, s.y)
        if f:
            rrect(cr, 2, 2, w - 4, h - 4, 18); cr.set_source_rgba(ac[0], ac[1], ac[2], 60 / 255); cr.fill()
            rrect(cr, 2, 2, w - 4, h - 4, 18); cr.set_source_rgba(*ac); cr.set_line_width(4); cr.stroke()
        self.txt.draw(cr, s.fmt(), w / 2, 42, 27 if f else 23, C(255, 255, 255) if f else C(200, 200, 215), mode="base", align="center", fake_bold=f)
        top, bot = s.track(); cx = w / 2; tw = 10.0
        rrect(cr, cx - tw / 2, top, tw, bot - top, tw); cr.set_source_rgba(*C(48, 48, 66)); cr.fill()
        zero = s.min if s.percent else (0 if s.min < 0 else s.min)
        yz = bot - (zero - s.min) / (s.max - s.min) * (bot - top); yv = bot - (s.value - s.min) / (s.max - s.min) * (bot - top)
        if abs(yz - yv) > 0.01:
            g = cairo.LinearGradient(0, bot, 0, top); g.add_color_stop_rgba(0, *ac); g.add_color_stop_rgba(1, *C(255, 120, 200))
            rrect(cr, cx - tw / 2, min(yz, yv), tw, abs(yz - yv), tw); cr.set_source(g); cr.fill()
        if not s.percent and s.min < 0:
            cr.set_source_rgba(*C(110, 110, 135)); cr.rectangle(cx - 16, yz - 1.5, 32, 3); cr.fill()
        cr.set_source_rgba(*(C(255, 255, 255) if f else ac)); cr.arc(cx, yv, 17 if f else 13, 0, 2 * math.pi); cr.fill()
        if f:
            cr.set_source_rgba(*ac); cr.arc(cx, yv, 8, 0, 2 * math.pi); cr.fill()
        ls = s.label.split("\n")
        for i, l in enumerate(ls):
            self.txt.draw(cr, l, w / 2, h - 30 - (len(ls) - 1 - i) * 27, 25 if f else 21, C(255, 255, 255) if f else C(160, 160, 185),
                          mode="base", align="center", fake_bold=f)
        cr.restore()

    def draw_bt(self, cr):
        text, spans = self.bt_text
        tw = min(self.txt.width(text, 20), 440 - 30)
        w = tw + 30; h = (TOP_K + BOT_K) * 20 + 12; x = VW - 20 - w; y = 14
        rrect(cr, x, y, w, h, 14); cr.set_source_rgba(*BTN_BG); cr.fill()
        self.txt.draw(cr, text, x + 14, y + 6, 20, C(220, 220, 235), spans=spans)

    def draw_toast(self, cr):
        msg, t0, dur = self.toast; el = time.monotonic() - t0
        a = min(1.0, el / 0.15, max(0.0, (dur - el) / 0.25))
        size = 28; tw = min(self.txt.width(msg, size), 1400); w = tw + 64; h = 44 + size * 1.17
        x = (VW - w) / 2; y = VH - 128 - h
        cr.save(); cr.push_group()
        rrect(cr, x, y, w, h, h / 2); cr.set_source_rgba(*C(56, 56, 60, 240)); cr.fill()
        self.txt.draw(cr, msg, VW / 2, y + h / 2, size, C(240, 240, 240), mode="center", align="center")
        cr.pop_group_to_source(); cr.paint_with_alpha(a); cr.restore()

    # ---------- okna dialogowe w stylu Androida (ciemny AlertDialog z Fire TV) ----------
    def open_dialog(self, kind, k=0):
        d = {"kind": kind, "k": k, "sel": 0}
        if kind == "save":
            d["title"] = T.t("Zapisz obecne ustawienia jako…", "Save current settings as…", "現在の設定の保存先…")
            d["items"] = [T.preset("Moje %d" % i) for i in (1, 2, 3)]
            d["buttons"] = [T.t("Anuluj", "Cancel", "キャンセル")]
        else:
            d["title"] = T.t("Zmień nazwę: ", "Rename: ", "名前を変更: ") + T.preset("Moje %d" % k)
            dn = T.def_name(k)
            d["msg"] = T.t("Maks. 16 znaków. Puste pole = „%s”." % dn, "Max 16 characters. Empty = “%s”." % dn, "最大16文字。空欄で「%s」に戻ります。" % dn)
            d["text"] = self.st.names.get(str(k)) or dn; d["selall"] = True
            d["buttons"] = [T.t("Anuluj", "Cancel", "キャンセル"), "OK"]; d["sel"] = 0
            d["cursor"] = True; GLib.timeout_add(500, self._blink)
        self.dialog = d; self.redraw()

    def _blink(self):
        if not self.dialog or self.dialog["kind"] != "rename":
            return False
        self.dialog["cursor"] = not self.dialog["cursor"]; self.redraw(); return True

    def dialog_layout(self):
        d = self.dialog; W = 992.0; x = (VW - W) / 2; pad = 48.0
        rows = []  # (typ, y_rel)
        yb = 81.0  # linia bazowa tytułu
        if d["kind"] == "save":
            ytop = yb + 35; d["item_rects"] = []
            for i in range(len(d["items"])):
                d["item_rects"].append((x, ytop + i * 96, W, 96)); 
            btn_b = ytop + 3 * 96 + 16 + 54
        else:
            lo = self.txt.layout(d["msg"], 32, W_REG, W - 2 * pad, False, Pango.Alignment.LEFT)
            nlines = lo.get_line_count(); msg_b = yb + 61; last = msg_b + (nlines - 1) * 37.5
            edit_b = last + 30 + 50
            d["msg_b"], d["edit_b"] = msg_b, edit_b
            btn_b = edit_b + 48 + 77
        H = btn_b + 45; y = (VH - H) / 2
        d["box"] = (x, y, W, H); d["btn_b"] = y + btn_b
        # przyciski od prawej
        rects = []; right = x + W - 47
        for i in range(len(d["buttons"]) - 1, -1, -1):
            lab = d["buttons"][i].upper(); tw = self.txt.width(lab, 28, W_MED)
            rects.insert(0, (right - tw - 16, y + btn_b - 42, tw + 32, 60)); right -= tw + 49
        d["btn_rects"] = rects
        if d["kind"] == "save":
            d["item_rects"] = [(rx, y + ry, rw, rh) for (rx, ry, rw, rh) in d["item_rects"]]
        return x, y, W, H

    def draw_dialog(self, cr):
        d = self.dialog; x, y, W, H = self.dialog_layout(); pad = 48.0; tx = self.txt
        cr.set_source_rgba(0, 0, 0, 0.6); cr.paint()
        rrect(cr, x, y, W, H, 4); cr.set_source_rgba(*C(66, 66, 66)); cr.fill()
        tx.draw(cr, d["title"], x + pad, y + 81, 40, C(255, 255, 255), W_MED, mode="base", width=W - 2 * pad, ellipsize=True)
        n_items = len(d.get("items", []))
        if d["kind"] == "save":
            for i, (rx, ry, rw, rh) in enumerate(d["item_rects"]):
                if d["sel"] == i and self.key_mode:
                    cr.set_source_rgba(1, 1, 1, 0.12); cr.rectangle(rx, ry, rw, rh); cr.fill()
                tx.draw(cr, d["items"][i], rx + pad, ry + rh / 2, 32, C(255, 255, 255, 222), mode="center")
        else:
            tx.draw(cr, d["msg"], x + pad, y + d["msg_b"], 32, C(255, 255, 255, 179), mode="base", width=W - 2 * pad)
            ex = x + pad + 28 - 28; ew = W - 2 * pad; eb = y + d["edit_b"]
            t = d["text"]
            if t:
                tw = tx.width(t, 30)
                if d.get("selall"):
                    cr.set_source_rgba(*C(128, 203, 196, 110)); cr.rectangle(ex + 8, eb - 30, tw, 40); cr.fill()
                tx.draw(cr, t, ex + 8, eb, 30, C(255, 255, 255), mode="base")
            else:
                tw = 0; tx.draw(cr, T.def_name(d["k"]), ex + 8, eb, 30, C(255, 255, 255, 110), mode="base")
            if d["cursor"] and not d.get("selall"):
                cr.set_source_rgba(*C(128, 203, 196)); cr.rectangle(ex + 8 + tw + 1, eb - 30, 3, 40); cr.fill()
            cr.set_source_rgba(*C(128, 203, 196)); cr.rectangle(ex, eb + 18, ew, 4); cr.fill()
        for i, (bx, by, bw, bh) in enumerate(d["btn_rects"]):
            si = n_items + i if d["kind"] == "save" else i + 1
            if self.key_mode and d["sel"] == si:
                rrect(cr, bx, by, bw, bh, 4); cr.set_source_rgba(1, 1, 1, 0.12); cr.fill()
            tx.draw(cr, d["buttons"][i].upper(), bx + bw / 2, d["btn_b"], 28, C(128, 203, 196), W_MED, mode="base", align="center")

    def dialog_choose(self, idx):
        """save: 0..2 = Moje 1–3, 3 = Anuluj; rename: 0 = pole, 1 = Anuluj, 2 = OK."""
        d = self.dialog
        if d["kind"] == "save":
            self.dialog = None
            if idx < 3:
                self.st.save_user(idx + 1); self.commit()
                self.show_toast(T.t("Zapisano jako ", "Saved as ", "保存しました: ") + T.preset("Moje %d" % (idx + 1)))
        else:
            if idx == 2:
                self.st.set_user_name(d["k"], d["text"]); self.dialog = None; self.commit()
            elif idx == 1:
                self.dialog = None
        self.redraw()

    def show_toast(self, msg, long=False):
        self.toast = (msg, time.monotonic(), 3.5 if long else 2.0); self.kick_anim()

    # ---------- akcje (jak w MainActivity) ----------
    def activate(self, iid):
        st = self.st
        if iid == "master":
            st.master = not st.master
            if st.master:
                self.engine.start(st, save_cb=st.save)
            else:
                self.engine.stop(st)
            self.commit()
        elif iid.startswith("p"):
            st.apply_builtin(int(iid[1])); self.sync_sliders(); self.commit()
        elif iid.startswith("u"):
            k = int(iid[1])
            if st.load_user(k):
                self.sync_sliders(); self.commit()
            else:
                self.show_toast(T.t("Moje %d jest puste – najpierw zapisz" % k, "My %d is empty – save first" % k, "マイ%d は空です – 先に保存してください" % k))
        elif iid == "save":
            self.open_dialog("save")
        elif iid == "lang":
            st.lang = T.next(T.lang); T.set_lang(st.lang); self.txt.set_lang(T.lang); self.set_title(T.app()); self.commit()
        elif iid == "bg":
            self.old_bg = st.bg; n = BG_COUNT + (1 if self.custom else 0); st.bg = st.bg % n + 1; st.save(); self.bg_fade = time.monotonic(); self.refresh_ui(); self.kick_anim()
        elif iid == "viz":
            st.viz = not st.viz; st.save(); self.start_viz(); self.refresh_ui()
        elif iid == "browse":
            self.browse()
        elif iid == "remove":
            self.remove_custom()

    # ---------- własny obraz w miejscu grafiki Yuki („Przeglądaj” / „Usuń”) ----------
    def art_btns(self):
        if not hasattr(self, "_browse"):
            self._browse = Button("browse", 20, 18, 0, 44, 20); self._remove = Button("remove", 0, 18, 0, 44, 20)
            self.all["browse"] = self._browse; self.all["remove"] = self._remove
        b, r = self._browse, self._remove
        b.text = T.t("📂 Przeglądaj…", "📂 Browse…", "📂 参照…"); b.w = self.txt.width(b.text, 20, W_MED) + 32
        out = [b]
        if self.custom and not getattr(self, "custom_gone", False):
            r.text = T.t("🗑 Usuń", "🗑 Remove", "🗑 削除"); r.w = self.txt.width(r.text, 20, W_MED) + 32; r.x = b.x + b.w + 10
            out.append(r)
        return out

    def draw_art_btn(self, cr, b):
        f = self.focused(b.id)
        rrect(cr, b.x, b.y, b.w, b.h, 14); cr.set_source_rgba(*C(32, 30, 48, 150)); cr.fill()
        if f:
            rrect(cr, b.x + 2, b.y + 2, b.w - 4, b.h - 4, 12); cr.set_source_rgba(*CYAN); cr.set_line_width(4); cr.stroke()
        self.txt.draw(cr, b.text, b.x + b.w / 2, b.y + b.h / 2, b.size, C(220, 220, 235, 230) if not f else C(255, 255, 255), W_MED, mode="center", align="center")

    def load_custom(self):
        self.custom = None
        try:
            if os.path.exists(CUSTOM_BG):
                src = cairo.ImageSurface.create_from_png(CUSTOM_BG)
                self.custom = src
        except Exception as e:
            print("własny obraz:", e, file=sys.stderr)

    def browse(self):
        dlg = Gtk.FileChooserNative.new(T.t("Wybierz obraz", "Choose an image", "画像を選択"), self, Gtk.FileChooserAction.OPEN,
                                        T.t("Otwórz", "Open", "開く"), T.t("Anuluj", "Cancel", "キャンセル"))
        flt = Gtk.FileFilter(); flt.set_name(T.t("Obrazy (jpg, png, webp)", "Images (jpg, png, webp)", "画像 (jpg, png, webp)"))
        for pat in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG", "*.JPEG", "*.PNG", "*.WEBP"):
            flt.add_pattern(pat)
        dlg.add_filter(flt)
        pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pics and os.path.isdir(pics):
            dlg.set_current_folder(pics)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            dlg.destroy()
            if path:
                self.set_custom(path)
        else:
            dlg.destroy()

    def set_custom(self, path):
        """Kopiuje wybrany obraz (tylko odczyt oryginału) do ~/.config/yuki-equalizer/wlasny-obraz.png, dopasowany jak grafika Yuki."""
        try:
            make_custom_png(path, CUSTOM_BG)
        except Exception as e:
            self.show_toast(T.t("Nie udało się wczytać obrazu", "Could not load the image", "画像を読み込めませんでした")); print(e, file=sys.stderr); return
        self.old_bg = self.st.bg; self.load_custom(); self.st.bg = BG_COUNT + 1; self.st.save(); self.bg_fade = time.monotonic(); self.refresh_ui(); self.kick_anim()

    def remove_custom(self):
        try:
            if os.path.exists(CUSTOM_BG):
                os.remove(CUSTOM_BG)   # usuwa tylko kopię aplikacji, nigdy oryginał
        except Exception:
            pass
        self.old_bg = self.st.bg; self.custom_gone = True
        if self.st.bg > BG_COUNT:
            self.st.bg = 1
        if self.focus == "remove":
            self.focus = "browse"
        GLib.timeout_add(130, self._drop_custom)
        self.st.save(); self.bg_fade = time.monotonic(); self.refresh_ui(); self.kick_anim()

    def _drop_custom(self):
        self.custom = None; self.custom_gone = False; self.redraw(); return False

    def rename(self, k):
        if not self.st.has_user(k):
            self.show_toast(T.t("Najpierw zapisz ustawienia przyciskiem „Zapisz jako własny”", "Save your settings with “Save as custom” first",
                                "先に「マイ設定として保存」で設定を保存してください")); return
        self.open_dialog("rename", k)

    def sync_sliders(self):
        for i in range(10):
            self.bands[i].set(self.st.gains[i])
        self.bass.set(self.st.bass); self.boost.set(self.st.boost)

    def on_slider(self, s):
        st = self.st
        if s is self.boost:
            st.boost = int(round(s.value))
        else:
            if s is self.bass:
                st.bass = float(s.value)
            else:
                st.gains[self.bands.index(s)] = float(s.value)
            if not st.preset.endswith("*"):
                st.preset += "*"
        self.commit()

    def save_soon(self):
        """Zapis ustawień najwyżej co 0,3 s (przy przeciąganiu suwaka nie piszemy pliku przy każdym ruchu myszy)."""
        if not getattr(self, "_save_src", None):
            def do():
                self._save_src = None; self.st.save(); return False
            self._save_src = GLib.timeout_add(300, do)

    def commit(self):
        self.save_soon()
        if self.st.master:
            self.engine.set_params(yuki_audio.params_from_state(self.st))
        self.viz.set_curve(self.st); self.refresh_ui()

    # ---------- wizualizer / statusy ----------
    def start_viz(self):
        if self.viz_tick:
            GLib.source_remove(self.viz_tick); self.viz_tick = None
        if self.st.viz and not self.iconified():
            self.viz.start(); self.viz_tick = GLib.timeout_add(33, self.viz_frame)
        else:
            self.viz.stop()
        self.redraw()

    def viz_frame(self):
        self.da.queue_draw_area(*self.dev_rect(0, 820, 608, 210))
        busy = (time.monotonic() - self.viz.quiet_since) < 1.5
        want = 33 if busy else 100
        if want != getattr(self, "_viz_rate", 33):
            self._viz_rate = want; self.viz_tick = GLib.timeout_add(want, self.viz_frame); return False
        return True

    def dev_rect(self, x, y, w, h):
        sc, ox, oy, _, _ = self.geom()
        return int(ox + x * sc) - 1, int(oy + y * sc) - 1, int(w * sc) + 3, int(h * sc) + 3

    def iconified(self):
        w = self.get_window()
        return bool(w and (w.get_state() & Gdk.WindowState.ICONIFIED))

    def on_wstate(self, w, ev):
        if ev.changed_mask & Gdk.WindowState.ICONIFIED:
            self.start_viz()

    def status_tick(self):
        master = self.st.master
        def bg():
            k = self.engine.refresh_status(master)
            mon = yuki_audio.default_sink() if self.viz.running else None
            GLib.idle_add(self._status_done, k, mon)
        threading.Thread(target=bg, daemon=True).start()
        return True

    def _status_done(self, k, mon):
        changed = k != self.status_key; self.status_key = k
        if mon is not None and self.viz.running and mon != self.viz.monitor:
            self.start_viz()  # domyślne wyjście się zmieniło – słuchamy nowego monitora
        if changed:
            self.redraw()
        return False

    def bt_tick(self):
        def bg():
            res = None
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                r = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                                  GLib.VariantType.new("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 2000, None)
                objs = r.unpack()[0]
                for path, ifs in objs.items():
                    dev = ifs.get("org.bluez.Device1")
                    if not dev or not dev.get("Connected"):
                        continue
                    icon = dev.get("Icon", "")
                    if not icon.startswith("audio"):
                        continue
                    name = dev.get("Alias") or dev.get("Name") or ""
                    lvl = ifs.get("org.bluez.Battery1", {}).get("Percentage", -1)
                    res = (name, int(lvl)); break
            except Exception:
                res = None
            GLib.idle_add(self._bt_done, res)
        threading.Thread(target=bg, daemon=True).start()
        return True

    def _bt_done(self, res):
        if not res:
            new = None
        else:
            name, lvl = res
            mx = 14 if lvl < 0 else 18
            name = name.strip(); name = name if len(name) <= mx else name[:max(1, mx - 1)] + "…"
            if lvl < 0:
                new = ("🎧 " + name + " · 🔌 " + T.t("brak danych o baterii", "no battery data", "バッテリー情報なし"), None)
            else:
                head = "🎧 " + name + " · 🔋 "; pct = "%d%%" % lvl
                col = GREEN if lvl > 30 else YELLOW if lvl >= 15 else RED
                hb = len(head.encode()); new = (head + pct, [(hb, hb + len(pct.encode()), col)])
        if new != self.bt_text:
            self.bt_text = new; self.redraw()
        return False

    # ---------- animacje ----------
    def kick_anim(self):
        if not self.anim_on:
            self.anim_on = True; self._anim_t = time.monotonic(); GLib.timeout_add(16, self.anim_step)

    def anim_step(self):
        now = time.monotonic(); dt = now - self._anim_t; self._anim_t = now; more = False
        for it in self.all.values():
            tgt = 1.0 if self.focused(it.id) else 0.0
            if it.anim != tgt:
                st = dt / 0.12
                it.anim = min(tgt, it.anim + st) if tgt > it.anim else max(tgt, it.anim - st); more = True
        if self.bg_fade is not None:
            e = now - self.bg_fade
            if e < 0.12:
                self.bg_alpha = 1 - e / 0.12; self._shown_bg = getattr(self, "_shown_bg", self.st.bg)
            elif e < 0.30:
                self.bg_alpha = (e - 0.12) / 0.18
            else:
                self.bg_alpha = 1.0; self.bg_fade = None
            more = True
        if self.toast:
            if now - self.toast[1] > self.toast[2]:
                self.toast = None
            more = True
        self.redraw()
        if not more:
            self.anim_on = False
        return more

    # ---------- mysz ----------
    def to_v(self, ev):
        sc, ox, oy, _, _ = self.geom()
        return (ev.x - ox) / sc, (ev.y - oy) / sc

    def hit(self, vx, vy):
        for it in self.art_btns() + self.items + self.sliders:
            if it.hit(vx, vy):
                return it
        return None

    def on_press(self, w, ev):
        self.da.grab_focus()
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True
        vx, vy = self.to_v(ev)
        if self.dialog:
            self.dialog_click(vx, vy); return True
        it = self.hit(vx, vy)
        if not it:
            return True
        self.key_mode = False
        if isinstance(it, Slider):
            self.focus = it.id; self.drag = it; self.slide_to(it, vy); self.kick_anim(); return True
        if ev.button == 3 and it.id.startswith("u"):
            self.rename(int(it.id[1])); return True
        if ev.button == 1:
            self.press = (it, time.monotonic(), False)
            if it.id.startswith("u"):
                GLib.timeout_add(500, self.long_press_check, it)
        self.kick_anim()
        return True

    def long_press_check(self, it):
        if self.press and self.press[0] is it and not self.press[2]:
            self.press = (it, self.press[1], True); self.rename(int(it.id[1]))
        return False

    def on_release(self, w, ev):
        vx, vy = self.to_v(ev)
        if self.drag:
            self.drag = None; return True
        if self.press:
            it, t0, done = self.press; self.press = None
            if not done and it.hit(vx, vy):
                self.activate(it.id)
        return True

    def on_motion(self, w, ev):
        if self.drag:
            self.slide_to(self.drag, self.to_v(ev)[1])
        return True

    def slide_to(self, s, vy):
        v = s.value_at(vy)
        if v != s.value:
            s.set(v); self.on_slider(s)

    def on_scroll(self, w, ev):
        if self.dialog:
            return True
        it = self.hit(*self.to_v(ev))
        if isinstance(it, Slider):
            d = 0
            if ev.direction == Gdk.ScrollDirection.UP:
                d = 1
            elif ev.direction == Gdk.ScrollDirection.DOWN:
                d = -1
            elif ev.direction == Gdk.ScrollDirection.SMOOTH:
                dy = ev.get_scroll_deltas()[2]; d = -1 if dy > 0 else 1 if dy < 0 else 0
            if d:
                self.focus = it.id; self.key_mode = False
                it.set(it.value + d * it.step); self.on_slider(it); self.kick_anim()
        return True

    def dialog_click(self, vx, vy):
        d = self.dialog; x, y, W, H = d["box"]
        if not (x <= vx <= x + W and y <= vy <= y + H):
            self.dialog = None; self.redraw(); return  # klik poza oknem = anuluj (jak na Androidzie)
        if d["kind"] == "save":
            for i, (rx, ry, rw, rh) in enumerate(d["item_rects"]):
                if rx <= vx <= rx + rw and ry <= vy <= ry + rh:
                    self.dialog_choose(i); return
            for i, (bx, by, bw, bh) in enumerate(d["btn_rects"]):
                if bx <= vx <= bx + bw and by <= vy <= by + bh:
                    self.dialog_choose(3 + i); return
        else:
            for i, (bx, by, bw, bh) in enumerate(d["btn_rects"]):
                if bx <= vx <= bx + bw and by <= vy <= by + bh:
                    self.dialog_choose(1 + i); return
            d["selall"] = False; self.redraw()

    # ---------- klawiatura (strzałki = pilot) ----------
    OK_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space)

    def on_key(self, w, ev):
        k = ev.keyval
        if k == Gdk.KEY_F11:
            w_ = self.get_window()
            if w_ and (w_.get_state() & Gdk.WindowState.FULLSCREEN):
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        if self.dialog:
            return self.dialog_key(ev)
        arrows = (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down)
        if k not in arrows and k not in self.OK_KEYS:
            return False
        if not self.key_mode and self.focus not in self.slider_ids:
            self.key_mode = True; self.kick_anim()
            if k in arrows:
                return True
        self.key_mode = True
        f = self.focus
        if k in self.OK_KEYS:
            if f in self.slider_ids:
                return True
            if self.ok_down is None:
                self.ok_down = [f, time.monotonic(), False]
            elif f.startswith("u") and not self.ok_down[2] and time.monotonic() - self.ok_down[1] > 0.5:
                self.ok_down[2] = True; self.rename(int(f[1]))
            return True
        if f in self.slider_ids:
            s = self.all[f]; i = self.slider_ids.index(f)
            if k == Gdk.KEY_Up:
                s.set(s.value + s.step); self.on_slider(s)
            elif k == Gdk.KEY_Down:
                s.set(s.value - s.step); self.on_slider(s)
            elif k == Gdk.KEY_Left:
                self.focus = "master" if i == 0 else self.slider_ids[i - 1]
            elif k == Gdk.KEY_Right:
                self.focus = self.slider_ids[min(i + 1, len(self.slider_ids) - 1)]
        elif f in ("browse", "remove"):
            if k == Gdk.KEY_Right:
                self.focus = "remove" if (f == "browse" and len(self.art_btns()) > 1) else "master"
            elif k == Gdk.KEY_Left and f == "remove":
                self.focus = "browse"
        else:
            i = self.left_col.index(f)
            if k == Gdk.KEY_Up:
                self.focus = self.left_col[max(0, i - 1)]
            elif k == Gdk.KEY_Down:
                self.focus = self.left_col[min(len(self.left_col) - 1, i + 1)]
            elif k == Gdk.KEY_Right:
                self.focus = "b0"
            elif k == Gdk.KEY_Left and f == "master":
                self.focus = "browse"
        self.kick_anim(); return True

    def on_key_up(self, w, ev):
        if ev.keyval in self.OK_KEYS and self.ok_down and not self.dialog:
            f, t0, done = self.ok_down; self.ok_down = None
            if not done and f == self.focus:
                self.activate(f)
            return True
        self.ok_down = None
        return False

    def dialog_key(self, ev):
        d = self.dialog; k = ev.keyval
        if k == Gdk.KEY_Escape:
            self.dialog = None; self.redraw(); return True
        self.key_mode = True
        if d["kind"] == "save":
            n = 4
            if k == Gdk.KEY_Up:
                d["sel"] = max(0, d["sel"] - 1)
            elif k == Gdk.KEY_Down:
                d["sel"] = min(n - 1, d["sel"] + 1)
            elif k in self.OK_KEYS:
                self.ok_down = None; self.dialog_choose(d["sel"])
            self.redraw(); return True
        # rename: 0 = pole tekstowe, 1 = Anuluj, 2 = OK
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.ok_down = None
            if d["sel"] == 0:
                d["sel"] = 2
            else:
                self.dialog_choose(d["sel"])
            self.redraw(); return True
        if d["sel"] > 0:
            if k == Gdk.KEY_Left:
                d["sel"] = 1
            elif k == Gdk.KEY_Right:
                d["sel"] = 2
            elif k == Gdk.KEY_Up:
                d["sel"] = 0
            elif k == Gdk.KEY_space:
                self.dialog_choose(d["sel"])
            self.redraw(); return True
        if k == Gdk.KEY_Down:
            d["sel"] = 2
        elif k == Gdk.KEY_BackSpace:
            d["text"] = "" if d.get("selall") else d["text"][:-1]; d["selall"] = False
        else:
            ch = chr(Gdk.keyval_to_unicode(k)) if Gdk.keyval_to_unicode(k) else ""
            if ch and ch.isprintable() and not (ev.state & Gdk.ModifierType.CONTROL_MASK):
                base = "" if d.get("selall") else d["text"]
                if len(base) < NAME_MAX:
                    d["text"] = base + ch
                d["selall"] = False
        d["cursor"] = True; self.redraw(); return True

    def on_quit(self, *a):
        if getattr(self, "_save_src", None):
            GLib.source_remove(self._save_src); self._save_src = None; self.st.save()
        self.viz.stop(); Gtk.main_quit()


def make_custom_png(src, dst):
    """Obraz dowolnego rozmiaru -> 608×1080 (wypełnienie + przycięcie do środka) z tym samym zanikaniem
    do tła po prawej stronie, jakie ma wbudowana grafika Yuki."""
    from PIL import Image, ImageOps
    im = Image.open(src); im = ImageOps.exif_transpose(im).convert("RGBA")
    im = ImageOps.fit(im, (608, 1080), Image.LANCZOS, centering=(0.5, 0.35))
    fade = Image.new("L", (608, 1080), 255); px = fade.load()
    for x in range(608 - 150, 608):
        a = int(255 * (608 - x) / 150.0)
        for y in range(1080):
            px[x, y] = a
    r, g, b, a0 = im.split()
    from PIL import ImageChops
    im.putalpha(ImageChops.multiply(a0, fade))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.png"; im.save(tmp); os.replace(tmp, dst)


def restore():
    """Tryb bez okna (do autostartu): jeśli korektor był WŁĄCZONY, uruchom filtr i przełącz wyjście."""
    st = State.load()
    if not st.master:
        print("Korektor Yuki: wyłączony – nic do zrobienia"); return
    for _ in range(60):  # po zalogowaniu PipeWire/słuchawki mogą jeszcze startować – czekamy do 30 s
        if yuki_audio._run(["pactl", "info"])[0] == 0 and (not st.out_sink or st.out_sink in yuki_audio.sinks()):
            break
        time.sleep(0.5)
    eng = yuki_audio.Engine(); eng.start(st, save_cb=st.save)
    for _ in range(100):
        time.sleep(0.1)
        if not eng.busy and eng.status != "starting":
            break
    time.sleep(0.5)
    print("Korektor Yuki: filtr uruchomiony, wyjście:", yuki_audio.default_sink())


def main():
    GLib.set_prgname("yuki-equalizer"); GLib.set_application_name("Korektor Yuki")
    if "--przywroc" in sys.argv:
        restore(); return
    if "--render" in sys.argv:  # test: wyrenderuj ekran 1920×1080 do PNG bez otwierania okna i bez dźwięku
        out = sys.argv[sys.argv.index("--render") + 1]
        os.environ["YUKI_OFFSCREEN"] = "1"
        app = App()
        if "--obraz" in sys.argv:
            make_custom_png(sys.argv[sys.argv.index("--obraz") + 1], "/tmp/yuki-test-obraz.png")
            app.custom = cairo.ImageSurface.create_from_png("/tmp/yuki-test-obraz.png"); app.st.bg = BG_COUNT + 1; app.refresh_ui()
        if "--fokus" in sys.argv:
            app.key_mode = True; app.focus = sys.argv[sys.argv.index("--fokus") + 1]; app.all[app.focus].anim = 1.0 if app.focus in app.all else 0
        if "--lang" in sys.argv:
            T.set_lang(sys.argv[sys.argv.index("--lang") + 1]); app.txt.set_lang(T.lang); app.refresh_ui()
        surf = cairo.ImageSurface(cairo.FORMAT_RGB24, 1920, 1080); cr = cairo.Context(surf)
        g = cairo.LinearGradient(0, 0, 1920, 1080)
        g.add_color_stop_rgb(0, 14 / 255, 12 / 255, 24 / 255); g.add_color_stop_rgb(0.5, 22 / 255, 16 / 255, 38 / 255); g.add_color_stop_rgb(1, 10 / 255, 10 / 255, 18 / 255)
        cr.set_source(g); cr.paint(); app.draw_scene(cr); surf.write_to_png(out); print("zapisano", out); return
    app = App(); app.show_all()
    if "--zrzut-okna" in sys.argv:  # test: zrzut prawdziwego okna z ekranu po 4 s, potem zamknięcie
        out = sys.argv[sys.argv.index("--zrzut-okna") + 1]
        def snap():
            w = app.get_window(); pb = Gdk.pixbuf_get_from_window(w, 0, 0, w.get_width(), w.get_height())
            pb.savev(out, "png", [], []); print("zrzut okna:", out, w.get_width(), "x", w.get_height()); app.destroy(); return False
        GLib.timeout_add(4000, snap)
    Gtk.main()


if __name__ == "__main__":
    main()

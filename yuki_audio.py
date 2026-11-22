"""Korektor Yuki (Linux) – silnik dźwięku: wirtualne wyjście PipeWire (filter-chain).

Łańcuch (jak na Fire TV):  wzmocnienie -> 10 pasm (31 Hz … 16 kHz) -> kompresor (tylko Mowa/Noc) -> Bas -> limiter -1 dB
Kompresor sc4m i limiter fast_lookahead_limiter pochodzą z pakietu swh-plugins (LADSPA); bez niego działa sam korektor.
Pasma odpowiadają wersji Fire TV: skrajne pasma to półki (31 Hz: <44 Hz, 16 kHz: >11,3 kHz), środkowe to filtry
szczytowe co oktawę. Filtr działa jako osobny proces `pipewire -c <conf>` uruchamiany przez `systemd-run --user`
(jednostka przejściowa `yuki-equalizer`, znika po zatrzymaniu i po restarcie komputera).
"""
import json, math, os, subprocess, threading, time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_DIR = os.environ.get("YUKI_CONFIG_DIR") or os.path.join(os.path.expanduser("~/.config"), "yuki-equalizer")
CHAIN_CONF = os.path.join(CONF_DIR, "filter-chain.conf")
SINK = "yuki_equalizer"            # nazwa węzła wejściowego (wirtualne wyjście audio)
OUT_NODE = "yuki_equalizer.output"  # wyjście filtra -> prawdziwe urządzenie
UNIT = "yuki-equalizer"
FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def cutoff(i):
    return math.sqrt(FREQS[i] * FREQS[i + 1]) if i < 9 else 20000.0


def _run(args, timeout=5):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # brak programu, timeout …
        return 1, "", str(e)


LADSPA_DIR = next((d for d in (os.environ.get("YUKI_LADSPA_DIR", ""), "/usr/lib/ladspa", "/usr/lib64/ladspa",
                                "/usr/lib/x86_64-linux-gnu/ladspa", "/usr/local/lib/ladspa")
                   if d and os.path.exists(os.path.join(d, "sc4m_1916.so"))), "/usr/lib/ladspa")
HAVE_SWH = all(os.path.exists(os.path.join(LADSPA_DIR, f)) for f in ("sc4m_1916.so", "fast_lookahead_limiter_1913.so"))
CH = ("L", "R")


def comp_ctl(on):
    """Kompresor jak MBC na Fire TV (Mowa/Noc): atak 5 ms, zwolnienie 150 ms, 4:1, próg -32 dB (sc4m: min. -30), kolano 6, +8 dB.
    Poza Mowa/Noc: stosunek 1:1, próg 0 dB, 0 dB = przezroczysty."""
    return {"Attack time (ms)": 5.0, "Release time (ms)": 150.0, "Ratio (1:n)": 4.0 if on else 1.0,
            "Threshold level (dB)": -30.0 if on else 0.0, "Knee radius (dB)": 6.0, "Makeup gain (dB)": 8.0 if on else 0.0}


def params_from_state(st):
    """Wartości sterowań filtra dla danego stanu (słownik 'węzeł:sterowanie' -> liczba), oba kanały."""
    p = {}
    for c in CH:
        p["boost_%s:Gain 1" % c] = round(st.boost / 100.0, 4)
        p["bass_%s:Gain" % c] = float(st.bass)
        for i in range(10):
            p["eq%d_%s:Gain" % (i, c)] = float(st.gains[i])
        if HAVE_SWH:
            for k, v in comp_ctl(st.comp).items():
                p["comp_%s:%s" % (c, k)] = v
    return p


def _desc(lang):
    return {"en": "Yuki Equalizer", "ja": "Yuki Equalizer"}.get(lang, "Korektor Yuki")


def _ctl(d):
    return "{ " + " ".join('"%s" = %s' % (k, v) for k, v in d.items()) + " }"


def write_conf(st, target):
    """Łańcuch jak na Fire TV: wzmocnienie -> 10 pasm -> kompresor (Mowa/Noc) -> bas -> limiter (-1 dB, zawsze na końcu)."""
    p = params_from_state(st); nodes = []; links = []
    for c in CH:
        nodes.append('{ type = builtin name = boost_%s label = mixer control = { "Gain 1" = %s } }' % (c, p["boost_%s:Gain 1" % c]))
        for i, f in enumerate(FREQS):
            g = p["eq%d_%s:Gain" % (i, c)]
            if i == 0:
                nodes.append('{ type = builtin name = eq0_%s label = bq_lowshelf control = { "Freq" = %.1f "Q" = 0.707 "Gain" = %s } }' % (c, cutoff(0), g))
            elif i == 9:
                nodes.append('{ type = builtin name = eq9_%s label = bq_highshelf control = { "Freq" = %.1f "Q" = 0.707 "Gain" = %s } }' % (c, cutoff(8), g))
            else:
                nodes.append('{ type = builtin name = eq%d_%s label = bq_peaking control = { "Freq" = %.1f "Q" = 1.414 "Gain" = %s } }' % (i, c, f, g))
        chain = ["boost_%s" % c] + ["eq%d_%s" % (i, c) for i in range(10)]
        if HAVE_SWH:
            nodes.append('{ type = ladspa name = comp_%s plugin = "%s/sc4m_1916.so" label = sc4m control = %s }'
                         % (c, LADSPA_DIR, _ctl(dict({"RMS/peak": 0.0}, **comp_ctl(st.comp)))))
            chain.append("comp_%s" % c)
        # Bas: pełne wzmocnienie poniżej ~90 Hz, połowa do ~180 Hz (jak na Fire TV)
        nodes.append('{ type = builtin name = bass_%s label = bq_lowshelf control = { "Freq" = 127.0 "Q" = 0.707 "Gain" = %s } }' % (c, p["bass_%s:Gain" % c]))
        chain.append("bass_%s" % c)
        for a, b in zip(chain, chain[1:]):
            out = "Output" if a.startswith("comp") else "Out"
            inp = "Input" if b.startswith("comp") else "In"
            links.append('{ output = "%s:%s" input = "%s:%s" }' % (a, out, b, inp))
    if HAVE_SWH:
        nodes.append('{ type = ladspa name = lim plugin = "%s/fast_lookahead_limiter_1913.so" label = fastLookaheadLimiter '
                     'control = { "Input gain (dB)" = 0.0 "Limit (dB)" = -1.0 "Release time (s)" = 0.06 } }' % LADSPA_DIR)
        links += ['{ output = "bass_L:Out" input = "lim:Input 1" }', '{ output = "bass_R:Out" input = "lim:Input 2" }']
        outs = '"lim:Output 1" "lim:Output 2"'
    else:
        outs = '"bass_L:Out" "bass_R:Out"'
    target_line = ('target.object = "%s"\n                node.dont-reconnect = true' % target) if target else ""
    txt = """# Wygenerowane automatycznie przez Korektor Yuki – nie edytuj ręcznie (nadpisywane przy każdym włączeniu).
context.properties = {
    log.level = 0
}
context.spa-libs = {
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}
context.modules = [
    { name = libpipewire-module-rt args = { } flags = [ ifexists nofail ] }
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-filter-chain
        args = {
            node.description = "%(desc)s"
            media.name       = "%(desc)s"
            filter.graph = {
                nodes = [
                    %(nodes)s
                ]
                links = [
                    %(links)s
                ]
                inputs  = [ "boost_L:In 1" "boost_R:In 1" ]
                outputs = [ %(outs)s ]
            }
            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {
                node.name   = "%(sink)s"
                media.class = Audio/Sink
            }
            playback.props = {
                node.name   = "%(out)s"
                node.passive = true
                %(target)s
            }
        }
    }
]
""" % {"desc": _desc(st.lang), "nodes": "\n                    ".join(nodes), "links": "\n                    ".join(links),
       "sink": SINK, "out": OUT_NODE, "target": target_line, "outs": outs}
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = CHAIN_CONF + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(txt)
    os.replace(tmp, CHAIN_CONF)


def sinks():
    rc, out, _ = _run(["pactl", "list", "short", "sinks"])
    return [l.split("\t")[1] for l in out.splitlines() if "\t" in l] if rc == 0 else []


def default_sink():
    rc, out, _ = _run(["pactl", "get-default-sink"])
    return out.strip() if rc == 0 else ""


def unit_active():
    rc, out, _ = _run(["systemctl", "--user", "is-active", UNIT])
    return out.strip() == "active"


def node_id(name=SINK):
    rc, out, _ = _run(["pw-dump", "Node"], timeout=8)
    if rc != 0:
        return None
    try:
        for o in json.loads(out):
            if o.get("type") == "PipeWire:Interface:Node" and o.get("info", {}).get("props", {}).get("node.name") == name:
                return o["id"]
    except Exception:
        pass
    return None


class Engine:
    """Włącza/wyłącza filtr i aktualizuje wzmocnienia na żywo (bez przerw w dźwięku)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None
        self.cv = threading.Condition()
        self.nid = None
        self.sent = {}
        self.busy = False
        self.status = "off"  # off | starting | attaching | active
        threading.Thread(target=self._param_worker, daemon=True).start()

    # --- włączanie / wyłączanie -------------------------------------------------------------
    def start(self, st, route=True, save_cb=None):
        def job():
            with self.lock:
                self.busy = True
                self.status = "starting"
                cur = default_sink()
                real = cur if cur and cur != SINK else (st.out_sink or "")
                if not real or real == SINK:
                    real = next((s for s in sinks() if s != SINK), "")
                write_conf(st, real)
                if not unit_active():
                    _run(["systemctl", "--user", "reset-failed", UNIT])
                    _run(["systemd-run", "--user", "--unit=" + UNIT, "--collect", "--quiet",
                          "-p", "Description=Korektor Yuki (filtr PipeWire)", "pipewire", "-c", CHAIN_CONF], timeout=10)
                self.status = "attaching"
                for _ in range(50):
                    if SINK in sinks():
                        break
                    time.sleep(0.1)
                self.nid = node_id(); self.sent = dict(params_from_state(st))
                if route and SINK in sinks():
                    if real:
                        st.out_sink = real
                        if save_cb:
                            save_cb()
                    _run(["pactl", "set-default-sink", SINK])
                self.busy = False
            self.set_params(params_from_state(st))
        threading.Thread(target=job, daemon=True).start()

    def stop(self, st, wait=False):
        def job():
            with self.lock:
                self.busy = True
                if default_sink() == SINK:
                    back = st.out_sink if st.out_sink in sinks() else ""
                    if back:
                        _run(["pactl", "set-default-sink", back])
                _run(["systemctl", "--user", "stop", UNIT])
                self.nid = None
                self.status = "off"
                self.busy = False
        t = threading.Thread(target=job, daemon=True)
        t.start()
        if wait:
            t.join(15)

    def refresh_status(self, master):
        """Wywoływane cyklicznie w tle – ustala tekst statusu (jak „aktywny ✓” na Fire TV)."""
        if self.busy:
            return self.status
        if not master:
            self.status = "off"
        elif SINK not in sinks():
            self.status = "starting"
        elif default_sink() != SINK:
            self.status = "attaching"
        else:
            self.status = "active"
        return self.status

    # --- zmiany suwaków na żywo -------------------------------------------------------------
    def set_params(self, params):
        with self.cv:
            self.pending = dict(params)
            self.cv.notify()

    def _param_worker(self):
        while True:
            with self.cv:
                while self.pending is None:
                    self.cv.wait()
                p, self.pending = self.pending, None
            if self.nid is None:
                self.nid = node_id(); self.sent = {}
            if self.nid is None:
                continue
            # tylko zmienione wartości, po 8 na wywołanie (pw-cli ma ograniczony bufor na jeden parametr Props)
            todo = [(k, v) for k, v in p.items() if self.sent.get(k) != v]
            for i in range(0, len(todo), 8):
                part = todo[i:i + 8]
                body = " ".join('"%s" %s' % (k, v) for k, v in part)
                rc, out, _ = _run(["pw-cli", "set-param", str(self.nid), "Props", "{ params = [ %s ] }" % body])
                if rc != 0 or "String" not in out:
                    self.nid = None; break
                self.sent.update(part)
            time.sleep(0.03)  # ~30 aktualizacji/s maks. podczas przeciągania

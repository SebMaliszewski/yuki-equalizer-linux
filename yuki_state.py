"""Stan korektora + presety – 1:1 z wersją Fire TV (State.java / T.java). Zapis: ~/.config/yuki-equalizer/state.json"""
import json, locale, math, os

CONF_DIR = os.environ.get("YUKI_CONFIG_DIR") or os.path.join(os.path.expanduser("~/.config"), "yuki-equalizer")
STATE_FILE = os.path.join(CONF_DIR, "state.json")
FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
NAMES = ["Płaski", "Muzyka", "Film", "Mowa/Noc", "Bas+"]
P_GAINS = [[0] * 10,
           [4, 3, 1, 0, -1, 0, 1, 2, 3, 3],
           [3, 2, 0, -1, 0, 2, 3, 2, 1, 0],
           [-6, -4, -2, 0, 2, 4, 4, 2, 0, -2],
           [8, 7, 5, 2, 0, 0, 0, 0, 1, 1]]
P_BASS = [0, 2, 3, 0, 6]
P_COMP = [False, False, False, True, False]
BG_COUNT = 3
CUSTOM_BG = os.path.join(CONF_DIR, "wlasny-obraz.png")  # kopia obrazu wybranego przez „Przeglądaj” (oryginał nietknięty)
NAME_MAX = 16


def sys_lang():
    l = (os.environ.get("LANG") or locale.getlocale()[0] or "").lower()
    return "ja" if l.startswith("ja") else "pl" if l.startswith("pl") else "en"


class State:
    def __init__(self):
        self.master = False; self.comp = False; self.gains = [0.0] * 10; self.bass = 0.0; self.boost = 100
        self.preset = "Płaski"; self.lang = sys_lang(); self.bg = 1; self.viz = True
        self.users = {}; self.names = {}; self.out_sink = ""

    @staticmethod
    def load():
        s = State()
        try:
            with open(STATE_FILE) as fh:
                d = json.load(fh)
            for k in ("master", "comp", "bass", "boost", "preset", "lang", "bg", "viz", "users", "names", "out_sink"):
                if k in d:
                    setattr(s, k, d[k])
            if isinstance(d.get("gains"), list) and len(d["gains"]) == 10:
                s.gains = [float(x) for x in d["gains"]]
        except FileNotFoundError:
            pass
        except Exception:
            pass
        if s.bg < 1 or s.bg > BG_COUNT + 1 or (s.bg == BG_COUNT + 1 and not os.path.exists(CUSTOM_BG)):
            s.bg = 1
        if s.lang not in ("pl", "en", "ja"):
            s.lang = "pl"
        return s

    def save(self):
        os.makedirs(CONF_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({k: getattr(self, k) for k in ("master", "comp", "gains", "bass", "boost", "preset", "lang", "bg", "viz", "users", "names", "out_sink")},
                      fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)

    def apply_builtin(self, i):
        self.gains = [float(x) for x in P_GAINS[i]]; self.bass = float(P_BASS[i]); self.comp = P_COMP[i]; self.preset = NAMES[i]

    def has_user(self, k):
        return str(k) in self.users

    def save_user(self, k):
        self.users[str(k)] = {"gains": list(self.gains), "bass": self.bass, "comp": self.comp}
        self.preset = "Moje %d" % k; self.save()

    def load_user(self, k):
        u = self.users.get(str(k))
        if not u:
            return False
        self.gains = [float(x) for x in u["gains"]]; self.bass = float(u["bass"]); self.comp = bool(u["comp"])
        self.preset = "Moje %d" % k; return True

    def set_user_name(self, k, raw):
        n = (raw or "").strip()
        if len(n) > NAME_MAX:
            n = n[:NAME_MAX].strip()
        if T.is_default_name(n, k):
            self.names.pop(str(k), None)
        else:
            self.names[str(k)] = n
        self.save()

    def boost_db(self):
        return 20 * math.log10(self.boost / 100.0)


class T:
    """Tabela tekstów PL / EN / JA (jak T.java)."""
    lang = "pl"; st = None

    @classmethod
    def set_lang(cls, l):
        cls.lang = l if l in ("en", "ja") else "pl"

    @staticmethod
    def next(l):
        return "en" if l == "pl" else "ja" if l == "en" else "pl"

    @classmethod
    def t(cls, pl, en, ja):
        return ja if cls.lang == "ja" else en if cls.lang == "en" else pl

    @classmethod
    def app(cls):
        return cls.t("Korektor Yuki", "Yuki Equalizer", "Yuki Equalizer")

    @classmethod
    def def_name(cls, k):
        return cls.t("Moje %d" % k, "My %d" % k, "マイ%d" % k)

    @staticmethod
    def is_default_name(n, k):
        x = n.strip().replace("\u3000", " ")
        fw = chr(0xFF10 + k)
        return (x == "" or x.lower() == ("moje %d" % k) or x.lower() == ("my %d" % k)
                or x in ("マイ%d" % k, "マイ %d" % k, "マイ" + fw, "マイ " + fw))

    @classmethod
    def preset(cls, stored):
        if stored is None:
            return ""
        mod = stored.endswith("*"); n = stored[:-1] if mod else stored
        slot = int(n[5]) if (len(n) == 6 and n.startswith("Moje ") and n[5] in "123") else 0
        names = cls.st.names if cls.st else {}
        if slot and names.get(str(slot)):
            o = names[str(slot)]
        elif cls.lang == "ja":
            o = {"Płaski": "フラット", "Muzyka": "ミュージック", "Film": "映画", "Mowa/Noc": "会話・夜間", "Bas+": "低音+"}.get(n, ("マイ" + n[5:]) if n.startswith("Moje ") else n)
        elif cls.lang == "pl":
            o = n
        else:
            o = {"Płaski": "Flat", "Muzyka": "Music", "Film": "Movie", "Mowa/Noc": "Speech/Night", "Bas+": "Bass+"}.get(n, ("My " + n[5:]) if n.startswith("Moje ") else n)
        return o + ("*" if mod else "")

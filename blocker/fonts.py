"""字型設定：優先使用 Chiron GoRound TC，沒安裝時自動退回系統繁中字型。

Chiron GoRound TC 的家族名結構（Windows / Tk 看到的名稱）：
    Chiron GoRound TC      → Regular(400) + Bold(700) 成對
    Chiron GoRound TC L    → Light(300)
    Chiron GoRound TC M    → Medium(500)
    Chiron GoRound TC SB   → SemiBold(600)
    Chiron GoRound TC N    → Normal(350)
    Chiron GoRound TC BK   → Book(450)
    Chiron GoRound TC H    → Heavy(900)
    Chiron GoRound TC EB   → ExtraBold(800)
    Chiron GoRound TC EL   → ExtraLight(200)
"""
import tkinter.font as tkfont


# 依序嘗試的字體家族（第一個是 Chiron GoRound TC）
FAMILY_CANDIDATES = ["Chiron GoRound TC"]

# 各種語意的字重家族
MEDIUM_CANDIDATES = ["Chiron GoRound TC M", "Chiron GoRound TC SB", "Chiron GoRound TC BK"]
SEMIBOLD_CANDIDATES = ["Chiron GoRound TC SB", "Chiron GoRound TC M", "Chiron GoRound TC BK"]

# 都找不到時的保底字型
FALLBACK_CANDIDATES = [
    "Microsoft JhengHei UI",   # Windows 繁體中文預設
    "Microsoft JhengHei",
    "PingFang TC",
    "Noto Sans TC",
    "Segoe UI",
]

# Tk 有時會把字體同時列成 "名字" 和 "@名字"，後者不能拿來指定
def _clean(families):
    return {f.lstrip("@").strip() for f in families}


def pick_family(available, candidates, fallbacks=None):
    """從可用字體裡挑第一個符合的家族名。"""
    avail = _clean(available)
    for c in candidates:
        if c in avail:
            return c
    for f in (fallbacks or []):
        if f in avail:
            return f
    return "TkDefaultFont"


class FontSet:
    """一次算好所有要用的字型設定。"""

    def __init__(self, root):
        fams = _clean(tkfont.families(root))
        self.available = fams

        self.family = pick_family(fams, FAMILY_CANDIDATES, FALLBACK_CANDIDATES)
        self.medium_family = pick_family(fams, MEDIUM_CANDIDATES, [self.family])
        self.semibold_family = pick_family(fams, SEMIBOLD_CANDIDATES, [self.family])

        # 設定好的話就是 True，沒有的話自動退回系統字型（別人的電腦可能沒裝）
        self.using_chiron = self.family == "Chiron GoRound TC"
        self.using_weighted = (self.medium_family != self.family
                               or self.semibold_family != self.family)

    # ---- 各處要用的字型組合 ----
    @property
    def body(self):
        return (self.family, 10)

    @property
    def body_small(self):
        return (self.family, 9)

    @property
    def bold(self):
        return (self.family, 10, "bold")

    @property
    def medium(self):
        return (self.medium_family, 10)

    @property
    def semibold(self):
        return (self.semibold_family, 10)

    @property
    def title(self):
        return (self.family, 11, "bold")

    @property
    def log(self):
        return (self.family, 10)

    def apply_tk_defaults(self, root):
        """覆寫 Tk 內建的具名字型，讓沒指定字型的元件也自動換字。"""
        import tkinter as tk
        plan = [
            ("TkDefaultFont", self.body),
            ("TkTextFont", self.body),
            ("TkMenuFont", self.body),
            ("TkHeadingFont", self.bold),
            ("TkTooltipFont", self.body_small),
            ("TkFixedFont", self.body),   # Entry 用這個
            ("TkIconFont", self.body),
            ("TkCaptionFont", self.body_small),
            ("TkSmallCaptionFont", self.body_small),
            ("TkStatusFont", self.body_small),
            ("TkMenuFont", self.body),
        ]
        for name, spec in plan:
            try:
                f = tkfont.nametofont(name)
                f.configure(family=spec[0], size=spec[1],
                            **({"weight": spec[2]} if len(spec) > 2 else {}))
            except Exception:
                pass

    def apply_ttk(self, root):
        """讓 ttk 元件也用上同一套字型。

        ttk 元件不支援在建構時傳 font=，一定要用 Style 設定。
        另外開幾個自訂樣式給需要加粗的元件用。
        """
        from tkinter import ttk
        try:
            style = ttk.Style(root)
        except Exception:
            return

        # 基礎字型：所有 ttk 元件都繼承這個
        for st in (".", "TLabel", "TCheckbutton", "TButton", "TNotebook.Tab",
                   "TEntry", "TCombobox", "TRadiobutton", "TFrame", "TLabelframe",
                   "TSeparator", "TNotebook"):
            try:
                style.configure(st, font=self.body)
            except Exception:
                pass

        # 自訂樣式：需要強調的地方套用
        try:
            style.configure("Em.TLabel", font=self.semibold)
            style.configure("Em.TCheckbutton", font=self.semibold)
            style.configure("Em.TButton", font=self.semibold)
            style.configure("H1.TLabel", font=self.title)
            style.configure("Hint.TLabel", font=self.body_small)
        except Exception:
            pass

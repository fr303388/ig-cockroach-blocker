"""蟑螂封鎖器 GUI —— 給不懂程式的人用的介面。

啟動方式：
    python main.py
打包成 EXE：
    build.bat

v0.10
  * Instagram 與 Threads 同時並行執行
  * 多語系介面（繁中 / 簡中 / 日本語 / English），右上角可切換，
    第一次啟動會跟著作業系統語言，之後記住你的選擇。
"""
import os
import sys
import queue
import threading
import datetime
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# PyInstaller 打包後，Playwright driver 預期瀏覽器在它旁邊的 .local-browsers，
# 但我們 build 時裝在系統 %LOCALAPPDATA%\ms-playwright。
# 明確指向系統路徑，必須在 import playwright 之前設定。
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    _local_app = os.environ.get("LOCALAPPDATA", "")
    if _local_app:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(_local_app, "ms-playwright")

# 注意：blocker.* 的 import 延後到 __init__ 裡做，
# 避免打包環境 import playwright 失敗時連 crash.log 都寫不出來。

VERSION = "0.10"


class App:
    def __init__(self, root: tk.Tk):
        # lazy import
        from blocker.config import Settings, load
        from blocker.browser import interactive_login
        from blocker.worker import run_round, scheduler_loop
        from blocker import i18n
        self._Settings = Settings
        self._interactive_login = interactive_login
        self._run_round = run_round
        self._scheduler_loop = scheduler_loop
        self._i18n = i18n
        self.t = i18n.t          # 譯文字串

        self.root = root
        # 依螢幕大小決定視窗尺寸，小螢幕也不會超出畫面
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self._win_w = max(500, min(700, sw - 80))
        self._win_h = max(400, min(580, sh - 120))
        self.root.geometry(f"{self._win_w}x{self._win_h}")
        self.root.minsize(480, 360)
        self.root.title(self.t("app_title"))

        self.s = load()
        self.log_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.login_done = {"instagram": False, "threads": False}
        self.login_saved = {"instagram": False, "threads": False}
        self.login_threads = {}
        self._set_canvas = None
        self._set_tab_index = 0
        self._running = False
        self._stopping = False
        # 需要重新翻譯的 widget：(widget, key, format kwargs)
        self._tr_widgets = []
        self._tab_keys = []

        # ---- 字型：優先 Chiron GoRound TC，沒裝就自動退回系統繁中字型 ----
        from blocker.fonts import FontSet
        self.fonts = FontSet(root)
        self.fonts.apply_tk_defaults(root)
        self.fonts.apply_ttk(root)
        self._font_family = self.fonts.family
        self._font_weighted = self.fonts.using_weighted
        self._font_chiron = self.fonts.using_chiron
        self._font_logged = False

        self._build_ui()
        self._poll_log()

    # ---------- 翻譯小工具 ----------
    def _tw(self, widget, key: str, **fmt):
        """設定文字並登記，之後切換語言時會自動重譯。"""
        widget.configure(text=self.t(key, **fmt))
        self._tr_widgets.append((widget, key, fmt))
        return widget

    def _font_log_line(self) -> str:
        if self._font_chiron:
            return self.t("font_chiron", family=self._font_family)
        return self.t("font_fallback", family=self._font_family)

    def _retranslate(self):
        """語言切換後，把所有已建立的控制項文字換掉（不重建視窗，保留紀錄）。"""
        self.root.title(self.t("app_title"))
        for w, key, fmt in self._tr_widgets:
            try:
                w.configure(text=self.t(key, **fmt))
            except Exception:
                pass
        for key, tid in zip(self._tab_keys, self._nb.tabs()):
            try:
                self._nb.tab(tid, text=self.t(key))
            except Exception:
                pass
        self._refresh_lang_box()
        self._refresh_status()

    def _lang_choices(self):
        """下拉選單的選項：先「跟隨系統」，再四種語言。"""
        n = self._i18n.LANG_NAMES
        return [("auto", self.t("lang_follow_system")),
                ("zh_TW", n["zh_TW"]), ("zh_CN", n["zh_CN"]),
                ("ja", n["ja"]), ("en", n["en"])]

    def _refresh_lang_box(self):
        if not hasattr(self, "lang_box"):
            return
        pairs = self._lang_choices()
        self.lang_box["values"] = [label for _, label in pairs]
        cur = self._lang_choice
        for i, (code, _) in enumerate(pairs):
            if code == cur:
                self.lang_box.current(i)
                return

    def on_lang_change(self, _event=None):
        pairs = self._lang_choices()
        i = self.lang_box.current()
        if i < 0 or i >= len(pairs):
            return
        code = pairs[i][0]
        if code == self._lang_choice:
            return
        self._lang_choice = code
        if code == "auto":
            self._i18n.set_lang("auto")
            self._i18n.save_preference("")
        else:
            self._i18n.set_lang(code)
            self._i18n.save_preference(code)
        self._retranslate()

    # ---------- 可捲動的設定頁 ----------
    def _make_scrollable(self, parent):
        """把設定內容包成可捲動區塊，視窗再矮也不會把選項卡在畫面外。"""
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        sbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)

        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            if event is not None and event.widget is canvas:
                canvas.itemconfigure(win_id, width=event.width)

        inner.bind("<Configure>", _sync)
        canvas.bind("<Configure>", _sync)
        canvas.configure(yscrollcommand=sbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        sbar.pack(side="right", fill="y")
        self._set_canvas = canvas
        return inner

    def _on_mouse_wheel(self, event):
        """只有在「設定」分頁被選取時才捲動，避免影響執行紀錄。"""
        if self._set_canvas is None or self._nb.index(self._nb.select()) != self._set_tab_index:
            return
        delta = int(-1 * (event.delta / 120)) if event.delta else 0
        if delta:
            self._set_canvas.yview_scroll(delta, "units")

    # ---------- UI ----------
    def _build_ui(self):
        self._nb = ttk.Notebook(self.root)
        # 注意：Notebook 最後才 pack，確保上下兩條工具列不會被擠掉

        # ==================== ① 設定 ====================
        tab_set_wrap = ttk.Frame(self._nb)
        self._nb.add(tab_set_wrap, text=self.t("tab_settings"))
        self._set_tab_index = self._nb.index(tab_set_wrap)
        tab_set = self._make_scrollable(tab_set_wrap)

        pad = {"padx": 8, "pady": 3}
        F = self.fonts
        row = 0

        self._tw(ttk.Label(tab_set, style="Em.TLabel"),
                 "lbl_keywords").grid(row=row, column=0, sticky="w", **pad)
        row += 1
        self.keywords_var = tk.StringVar(value=", ".join(self.s.keywords))
        ttk.Entry(tab_set, textvariable=self.keywords_var, width=60).grid(
            row=row, column=0, columnspan=2, sticky="we", **pad)
        row += 1

        self._tw(ttk.Label(tab_set), "lbl_max").grid(row=row, column=0, sticky="w", **pad)
        self.max_var = tk.IntVar(value=self.s.max_per_run)
        ttk.Entry(tab_set, textvariable=self.max_var, width=10).grid(
            row=row, column=1, sticky="w", **pad)
        row += 1

        self._tw(ttk.Label(tab_set), "lbl_max_try").grid(row=row, column=0, sticky="w", **pad)
        self.max_try_var = tk.IntVar(value=int(getattr(self.s, "max_try_per_run", 60) or 60))
        ttk.Entry(tab_set, textvariable=self.max_try_var, width=10).grid(
            row=row, column=1, sticky="w", **pad)
        row += 1

        self._tw(ttk.Label(tab_set), "lbl_delay").grid(row=row, column=0, sticky="w", **pad)
        self.delay_var = tk.IntVar(value=int(max(self.s.delay_min, self.s.delay_max)))
        ttk.Entry(tab_set, textvariable=self.delay_var, width=10).grid(
            row=row, column=1, sticky="w", **pad)
        row += 1

        self._tw(ttk.Label(tab_set), "lbl_schedule").grid(row=row, column=0, sticky="w", **pad)
        row += 1
        self.schedule_text = tk.Text(tab_set, width=40, height=2, font=F.body,
                                     relief="solid", borderwidth=1, highlightthickness=1)
        self.schedule_text.insert("1.0", "\n".join(self.s.schedule_times))
        self.schedule_text.grid(row=row, column=0, columnspan=2, sticky="we", **pad)
        row += 1

        self.skip_verified_var = tk.BooleanVar(value=self.s.skip_verified)
        self._tw(ttk.Checkbutton(tab_set, variable=self.skip_verified_var),
                 "chk_verified").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1
        self.skip_following_var = tk.BooleanVar(value=self.s.skip_following)
        self._tw(ttk.Checkbutton(tab_set, variable=self.skip_following_var),
                 "chk_following").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        # 數字欄位在左邊，說明文字在右邊
        fans_row = ttk.Frame(tab_set)
        fans_row.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        self.skip_fans_var = tk.IntVar(value=self.s.skip_follower_gt)
        ttk.Entry(fans_row, textvariable=self.skip_fans_var, width=10).pack(side="left")
        self._tw(ttk.Label(fans_row), "lbl_fans").pack(side="left", padx=(8, 0))
        row += 1

        self.block_future_var = tk.BooleanVar(value=self.s.block_future_accounts)
        self._tw(ttk.Checkbutton(tab_set, variable=self.block_future_var),
                 "chk_future").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        self.require_kw_var = tk.BooleanVar(value=self.s.require_keyword_match)
        self._tw(ttk.Checkbutton(tab_set, variable=self.require_kw_var),
                 "chk_require_kw").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        self.dry_run_var = tk.BooleanVar(value=self.s.dry_run)
        self._tw(ttk.Checkbutton(tab_set, variable=self.dry_run_var),
                 "chk_dryrun").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        self.debug_shot_var = tk.BooleanVar(value=self.s.debug_screenshots)
        self._tw(ttk.Checkbutton(tab_set, variable=self.debug_shot_var),
                 "chk_debug").grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        self.run_ig_var = tk.BooleanVar(value=self.s.run_instagram)
        self._tw(ttk.Checkbutton(tab_set, variable=self.run_ig_var),
                 "chk_ig").grid(row=row, column=0, sticky="w", **pad)
        row += 1
        self.run_th_var = tk.BooleanVar(value=self.s.run_threads)
        self._tw(ttk.Checkbutton(tab_set, variable=self.run_th_var),
                 "chk_th").grid(row=row, column=0, sticky="w", **pad)
        row += 1

        tab_set.columnconfigure(0, weight=1)

        # ==================== ② 執行紀錄 ====================
        tab_log = ttk.Frame(self._nb)
        self._nb.add(tab_log, text=self.t("tab_log"))
        self.log_text = scrolledtext.ScrolledText(tab_log, wrap="word", font=F.log)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_text.configure(state="disabled")

        # ==================== ③ 關於 ====================
        tab_about = ttk.Frame(self._nb)
        self._nb.add(tab_about, text=self.t("tab_about"))
        about_wrap = ttk.Frame(tab_about)
        about_wrap.pack(fill="both", expand=True, padx=12, pady=12, anchor="nw")
        self._about_label = self._tw(
            ttk.Label(about_wrap, justify="left", wraplength=self._win_w - 60),
            "about_text")
        self._about_label.pack(anchor="nw")
        ver = ttk.Label(about_wrap, text=f"v{VERSION}", style="Hint.TLabel")
        ver.pack(anchor="nw", pady=(8, 0))

        self._tab_keys = ["tab_settings", "tab_log", "tab_about"]

        # ---- 底部按鈕列：固定在視窗下緣，切到任何分頁都看得到、點得到 ----
        btn_bar = ttk.Frame(self.root)
        btn_bar.pack(side="bottom", fill="x", padx=10, pady=(2, 10))
        self._tw(ttk.Button(btn_bar, command=lambda: self.do_login("instagram")),
                 "btn_login_ig").pack(side="left", padx=3)
        self._tw(ttk.Button(btn_bar, command=lambda: self.do_login("threads")),
                 "btn_login_th").pack(side="left", padx=3)
        self._tw(ttk.Button(btn_bar, style="Em.TButton", command=self.run_now),
                 "btn_run_now").pack(side="left", padx=3)
        self.start_btn = self._tw(ttk.Button(btn_bar, style="Em.TButton",
                                             command=self.start_schedule), "btn_start")
        self.start_btn.pack(side="left", padx=3)
        self.stop_btn = self._tw(ttk.Button(btn_bar, command=self.stop, state="disabled"),
                                 "btn_stop")
        self.stop_btn.pack(side="left", padx=3)
        self.status_var = tk.StringVar(value=self.t("status_ready"))
        ttk.Label(btn_bar, textvariable=self.status_var, style="Hint.TLabel").pack(
            side="right", padx=6)

        # ---- 頂部列：左邊程式名、右邊語言下拉選單 ----
        top_bar = ttk.Frame(self.root)
        top_bar.pack(side="top", fill="x", padx=10, pady=(10, 0))
        self._tw(ttk.Label(top_bar, style="Em.TLabel"), "app_title").pack(side="left")
        self._tw(ttk.Label(top_bar, style="Hint.TLabel"), "lang_label").pack(
            side="right", padx=(8, 4))
        self._lang_choice = "auto" if self._i18n.is_following_system() else self._i18n.get_lang()
        self.lang_box = ttk.Combobox(top_bar, state="readonly", width=13,
                                     font=F.body)
        self.lang_box.pack(side="right")
        self.lang_box.bind("<<ComboboxSelected>>", self.on_lang_change)
        self._refresh_lang_box()

        # Notebook 最後 pack：會佔滿頂部列與按鈕列之間的空間
        self._nb.pack(side="top", fill="both", expand=True, padx=10, pady=(8, 4))

        # 滑鼠滾輪捲動設定頁
        self.root.bind("<MouseWheel>", self._on_mouse_wheel)

    # ---------- 邏輯 ----------
    def collect_settings(self):
        from blocker.config import save
        s = self._Settings()
        s.keywords = [k.strip() for k in self.keywords_var.get().split(",") if k.strip()]
        s.max_per_run = max(1, int(self.max_var.get()))
        # 嘗試上限：0 = 自動推導（至少 max_per_run 的 3 倍，不少於 30）
        try:
            s.max_try_per_run = max(0, int(self.max_try_var.get()))
        except Exception:
            s.max_try_per_run = 0
        # 單一數字控制：一律等固定秒數，不再隨機跳動
        try:
            secs = max(0, int(float(self.delay_var.get())))
        except Exception:
            secs = 6
        s.delay_min = float(secs)
        s.delay_max = float(secs)
        s.schedule_times = [x.strip() for x in
                            self.schedule_text.get("1.0", "end").splitlines() if x.strip()]
        s.skip_verified = self.skip_verified_var.get()
        s.skip_following = self.skip_following_var.get()
        s.skip_follower_gt = max(0, int(self.skip_fans_var.get()))
        s.block_future_accounts = self.block_future_var.get()
        s.require_keyword_match = self.require_kw_var.get()
        s.dry_run = self.dry_run_var.get()
        s.debug_screenshots = self.debug_shot_var.get()
        s.run_instagram = self.run_ig_var.get()
        s.run_threads = self.run_th_var.get()
        self._save = save
        return s

    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        if not self._font_logged:
            self._font_logged = True
            self.log(self._font_log_line())
        self.root.after(200, self._poll_log)

    # ---------- 登入 ----------
    def do_login(self, platform: str):
        if platform in self.login_threads and self.login_threads[platform].is_alive():
            messagebox.showinfo(
                self.t("login_in_progress"),
                self.t("login_hint", platform=platform))
            return
        self.login_done[platform] = False
        popup = tk.Toplevel(self.root)
        popup.title(self.t("login_title", platform=platform))
        popup.geometry("440x170")
        ttk.Label(popup, text=self.t("login_hint", platform=platform),
                  justify="center", font=self.fonts.body).pack(pady=14)

        def on_done():
            # 只標記「使用者說登入完了」，實際存檔結果由背景執行緒回報
            self.login_done[platform] = True
            popup.destroy()
            self.log(self.t("login_saving", platform=platform))

        ttk.Button(popup, text=self.t("login_done_btn"),
                   style="Em.TButton", command=on_done).pack(pady=6)

        def worker():
            try:
                self._interactive_login(platform, lambda: self.login_done[platform])
                # 存檔有成功才寫成功，否則回報失敗（不要假報）
                from blocker.browser import state_path_for
                sp = state_path_for(platform)
                if os.path.exists(sp) and os.path.getsize(sp) > 50:
                    self.login_saved[platform] = True
                    self.log(self.t("login_ok", platform=platform))
                else:
                    self.log(self.t("login_not_saved", platform=platform))
            except Exception as e:
                full = traceback.format_exc()
                self.log(self.t("login_failed", platform=platform, error=str(e)[:150]))
                try:
                    crash_log = os.path.join(
                        os.path.dirname(os.path.abspath(sys.argv[0])), "crash.log")
                    with open(crash_log, "a", encoding="utf-8") as f:
                        f.write(f"\n=== {platform} login {datetime.datetime.now()} ===\n{full}\n")
                except Exception:
                    pass
                # 回到主執行緒彈錯誤視窗
                def show_err():
                    messagebox.showerror(
                        self.t("login_err_title", platform=platform),
                        self.t("login_err_body", error=str(e)[:200]))
                self.root.after(0, show_err)

        th = threading.Thread(target=worker, daemon=True)
        self.login_threads[platform] = th
        th.start()

    # ---------- 執行 ----------
    def _confirm_real_blocking(self, s) -> bool:
        """要真的封人之前，再確認一次。預設走演練模式。"""
        if s.dry_run:
            return True
        return messagebox.askyesno(
            self.t("confirm_title"),
            self.t("confirm_body",
                   kw=", ".join(s.keywords),
                   mx=s.max_per_run,
                   fans=s.skip_follower_gt))

    def run_now(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(self.t("busy_title"), self.t("busy_body"))
            return
        s = self.collect_settings()
        if not self._confirm_real_blocking(s):
            self.log(self.t("cancelled"))
            return
        self._save(s)
        self.stop_event = threading.Event()
        self._set_running_ui(True)
        self.log(self.t("manual_run"))

        def w():
            try:
                self._run_round(s, self.log, self.stop_event)
            finally:
                self.root.after(0, lambda: self._set_running_ui(False))

        self.worker_thread = threading.Thread(target=w, daemon=True)
        self.worker_thread.start()

    def start_schedule(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        s = self.collect_settings()
        if not self._confirm_real_blocking(s):
            self.log(self.t("cancelled"))
            return
        self._save(s)
        self.stop_event = threading.Event()
        self._set_running_ui(True)
        self.log(self.t("schedule_on"))

        def w():
            try:
                self._scheduler_loop(s, self.log, self.stop_event)
            finally:
                self.root.after(0, lambda: self._set_running_ui(False))

        self.worker_thread = threading.Thread(target=w, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.stop_event.set()
        self._stopping = True
        self._refresh_status()
        self.log(self.t("stopping"))

    def _refresh_status(self):
        if not hasattr(self, "status_var"):
            return
        if getattr(self, "_stopping", False) and self._running:
            self.status_var.set(self.t("status_stopping"))
        else:
            self.status_var.set(self.t("status_running") if self._running
                                else self.t("status_ready"))

    def _set_running_ui(self, running: bool):
        self._running = running
        if not running:
            self._stopping = False
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._refresh_status()


def main():
    # --windowed 模式沒有 console，任何崩潰都會被吞掉。
    # 最外層包 try/except，把錯誤寫到 crash.log，方便除錯。
    crash_log = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "crash.log")
    try:
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception:
        try:
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        try:
            messagebox.showerror("啟動失敗 / Startup failed",
                                 f"請看 crash.log / see crash.log\n\n{traceback.format_exc()[:500]}")
        except Exception:
            pass


if __name__ == "__main__":
    main()

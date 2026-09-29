"""Threads 網頁版操作。

⚠ 實測紀錄（2026-09，未登入狀態下驗證）：
  * 網域已從 threads.net 搬到 **threads.com**，舊網址會 301 轉址。
  * /search?q= 免登入就能用，回傳的連結是 **貼文作者**（/@user 與 /@user/post/xxx），
    不是「帳號搜尋結果」，所以候選品質比 IG 差很多。
  * 個人主頁**沒有** IG 用的 svg[aria-label="選項"]，封鎖選單必須另外找。
  * 未登入時整頁被「透過 Threads 暢所欲言」登入視窗蓋住。

本模組的封鎖流程尚未用「已登入」狀態驗證過，第一次用請開演練模式並看 debug 截圖。
"""
import re
import random
from typing import Tuple, List

from .instagram import _Stopped
from .instagram import _Stopped, BLOCK_ITEM_RE, UNBLOCK_ITEM_RE
from .i18n import t

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .config import Settings
from .instagram import InstagramBlocker

# threads.net 會轉址到 threads.com，直接用新網域省一次往返
BASE = "https://www.threads.com"

# 未登入時會出現的登入視窗（整頁文字都要掃，所以放寬一點）
LOGIN_WALL_RE = re.compile(
    r"登入或註冊|透過\s*Threads\s*暢所欲言|加入\s*Threads|Log in or sign up|Log in to Threads",
    re.I,
)


class ThreadsBlocker(InstagramBlocker):
    PLATFORM = "threads"

    # ---------- 登入狀態檢查 ----------
    def _logged_in(self) -> bool:
        """Threads 未登入時會整頁蓋登入視窗，先確認有登入再往下做。"""
        try:
            body = self.page.inner_text("body")
        except Exception:
            return True   # 讀不到就先假設有，後面的檢查會擋
        if LOGIN_WALL_RE.search(body):
            self.log(t("r_login_expired", platform="Threads"))
            return False
        # 另一種訊號：頁面上有登入按鈕 / 登入連結
        try:
            if self.page.locator(
                    'a[href*="login" i], [role="button"]:has-text("登入"), '
                    '[role="button"]:has-text("Log in")').count() > 0:
                self.log(t("r_login_expired", platform="Threads"))
                return False
        except Exception:
            pass
        return True

    # ---------- 搜尋 ----------
    def _click_profiles_tab(self, retries: int = 6, wait_ms: int = 2000) -> bool:
        """切到「個人檔案」分頁。

        Threads 搜尋預設停在「最相關」，那裡全是**貼文**（作者跟關鍵字無關），
        必須切到「個人檔案」分頁才是真正的帳號搜尋結果。
        分頁不是 role=tab，只能靠「文字 + 座標」定位（並排除左側選單的同名項目）。
        分頁是前端渲染的，要重試等它出現。
        """
        for _ in range(max(1, retries)):
            try:
                spans = self.page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('span, div').forEach(e => {
                        const own = [...e.childNodes]
                            .filter(n => n.nodeType === 3)
                            .map(n => n.textContent.trim()).join('');
                        if (own !== '個人檔案' && own !== 'Profile') return;
                        const r = e.getBoundingClientRect();
                        if (r.width === 0) return;
                        out.push({x: Math.round(r.x), y: Math.round(r.y),
                                  w: Math.round(r.width), h: Math.round(r.height)});
                    });
                    return out;
                }""")
            except Exception:
                spans = None

            if spans:
                # 分頁在頁面上方靠右；左側選單也有「個人檔案」，用座標範圍排除
                for s in spans:
                    if not (80 <= s["y"] <= 150 and s["x"] > 250 and s["w"] > 0):
                        continue
                    try:
                        self.page.mouse.click(s["x"] + s["w"] // 2, s["y"] + s["h"] // 2)
                        self._wait(4000 / 1000)
                        # 確認真的切過去了：應該會多出很多 /@ 純帳號連結
                        n = self.page.evaluate(
                            "() => document.querySelectorAll('a[href^=\"/@\"]').length")
                        if n > 5:
                            self.log(f"  [Threads] switched to Profiles tab ({n} links)")
                            return True
                    except Exception:
                        continue
            if not self._wait(wait_ms / 1000):
                return False
        return False

    def search_accounts(self, keyword: str) -> List[str]:
        """Threads 搜尋：切到「個人檔案」分頁後才拿得到帳號清單。"""
        self._goto(f"{BASE}/search?q={keyword}")
        self._wait(6000 / 1000)

        if not self._logged_in():
            return []

        if not self._click_profiles_tab():
            self.log("  [Threads] no Profiles tab (UI may have changed); "
                 "falling back to post authors")

        usernames: List[str] = []
        links = self.page.query_selector_all('a[href^="/@"]')
        for a in links:
            href = a.get_attribute("href") or ""
            # 只要「純個人頁」連結，丟掉 /@user/post/xxx、/@user/post/xxx/media
            m = re.fullmatch(r"/@([A-Za-z0-9._]{1,30})/?", href)
            if not m:
                continue
            u = m.group(1)
            if not self._is_valid_username(u, keyword):
                continue
            if u not in usernames:
                usernames.append(u)

        self.log(f"[Threads] \"{keyword}\": {len(usernames)} candidates: {usernames[:10]}")
        if not usernames:
            self._debug_screenshot(f"nosearch_{keyword}")
        return usernames

    # ---------- 判斷 ----------
    def inspect_and_decide(self, username: str) -> Tuple[bool, str]:
        username = username.strip().strip("/").lstrip("@")
        if not username:
            return False, t("r_invalid_name")
        if username.lower() in self.already:
            return False, t("r_already_blocked")
        try:
            self._goto(f"{BASE}/@{username}")
        except _Stopped:
            return False, t("r_stopped")
        except PWTimeout:
            return False, t("r_open_timeout")
        except Exception as e:
            return False, t("r_open_fail")
        if not self._wait(random.uniform(2.0, 3.0)):
            return False, t("r_stopped")

        if not self._logged_in():
            return False, t("r_not_logged_in")

        # 帳號不存在
        try:
            head = self.page.inner_text("body")[:500]
        except Exception:
            head = ""
        if self.__class__.__mro__ and re.search(
                r"很抱歉|此頁面無法使用|Page Not Found|Sorry, this page", head, re.I):
            return False, t("r_not_exist")

        # 確認真的停在對方主頁
        if f"/@{username}".lower() not in self.page.url.lower().split("?")[0]:
            return False, t("r_wrong_page", url=self.page.url[:70])

        # 已封過
        try:
            if self.page.get_by_text(UNBLOCK_ITEM_RE).count() > 0:
                return False, t("r_th_blocked")
        except Exception:
            pass

        # 藍勾勾
        if self.s.skip_verified:
            try:
                if self.page.query_selector('svg[aria-label*="verified" i], svg[aria-label*="Verified" i]'):
                    return False, t("r_verified")
            except Exception:
                pass

        # 自己的主頁
        try:
            if self.page.get_by_text(re.compile(r"編輯個人檔案|編輯個人簡介|Edit profile", re.I)).count() > 0:
                self._own_username = username
                return False, t("r_own_profile")
        except Exception:
            pass

        # 已追蹤（用 _following_state，避開左側選單「其他動態消息 → 追蹤中」）
        if self.s.skip_following:
            state = self._following_state()
            if state == "following":
                return False, t("r_following")
            if state is None:
                self.log(t("r_no_follow", user=username))

        fans = self._read_followers()
        if fans is None:
            self.log(t("r_no_fans", user=username))
        elif fans >= self.s.skip_follower_gt:
            return False, t("r_fans_over", fans=f"{fans:,}", limit=f"{self.s.skip_follower_gt:,}")

        return True, (t("r_ok_with_fans", fans=f"{fans:,}") if fans else t("r_ok"))

    # ---------- 執行封鎖 ----------
    def _open_options_menu(self) -> bool:
        """開啟「對方」的「⋯」選單。

        實測（2026/09，1280x800）Threads 上有三顆長得很像的三點圖示：
          1. 左上「≡」漢堡  → svg[aria-label="更多"] → 開的是「自己的帳號選單」（外觀/設定/登出）
          2. 頂部「⋯」       → svg[aria-label="更多"] → 不是對方的選單
          3. **對方選單**    → svg[title="更多"]     → 在粉絲數旁邊，y 約 267
        所以一定要用 title= 篩（不是 aria-label），並限定 y < 400 排除貼文裡的「⋯」。
        選單容器是 role="menu"、項目是 role="menuitem"（**不是** role="dialog"）。
        """
        btn = self.page.locator('svg[title="更多"], svg[title="More"]')
        try:
            n = btn.count()
        except Exception:
            return False
        for i in range(n):
            try:
                box = btn.nth(i).bounding_box()
                if not box or box["y"] > 400:        # 排除貼文內的「⋯」
                    continue
                target = btn.nth(i).locator("xpath=ancestor::*[@role='button'][1]")
                if target.count() == 0:
                    target = btn.nth(i)
                target.first.click(force=True)
                self._wait(2000 / 1000)
                if self.page.locator('[role="menuitem"]').count() > 0:
                    self.log("  [Threads] opened the target options menu")
                    return True
                self.page.keyboard.press("Escape")
                self._wait(400 / 1000)
            except Exception:
                continue
        return False

    def _menu_items(self):
        n = self.page.locator('[role="menuitem"]').count()
        out = []
        for i in range(n):
            try:
                out.append((self.page.locator('[role="menuitem"]').nth(i).inner_text() or "").strip())
            except Exception:
                pass
        return out

    def block(self, username: str) -> Tuple[bool, str]:
        if self.s.dry_run:
            return True, t("dryrun_banner")
        try:
            if f"/@{username}".lower() not in self.page.url.lower().split("?")[0]:
                self._debug_screenshot(f"notprofile_{username}")
                return False, t("r_wrong_page", url=self.page.url[:60])
            if not self._logged_in():
                return False, t("r_not_logged_in")

            if not self._open_options_menu():
                self._debug_screenshot(f"nomenu_{username}")
                return False, ("could not find the target \"…\" menu "
                               "(Threads UI may have changed; please send the "
                               "screenshot in the debug folder)")

            # 精確比對整個文字，避免抓到「封鎖並檢舉」之類
            picked = None
            n = self.page.locator('[role="menuitem"]').count()
            for i in range(n):
                el = self.page.locator('[role="menuitem"]').nth(i)
                try:
                    if BLOCK_ITEM_RE.fullmatch((el.inner_text() or "").strip()):
                        picked = el
                        break
                except Exception:
                    continue
            if picked is None:
                self._debug_screenshot(f"noblock_{username}")
                return False, f"no Block item in menu; items: {self._menu_items()[:8]}"

            picked.click(force=True)
            self._wait(2500 / 1000)

            # 確認彈窗（role="dialog"，內有「封鎖」和「取消」）
            if self.page.locator('div[role="dialog"]').count() == 0:
                self._debug_screenshot(f"noconfirm_{username}")
                return False, "no confirm dialog"

            dialog = self.page.locator('div[role="dialog"]').last
            confirm = None
            for i in range(dialog.locator('div[role="button"], button').count()):
                btn = dialog.locator('div[role="button"], button').nth(i)
                try:
                    if BLOCK_ITEM_RE.fullmatch((btn.inner_text() or "").strip()):
                        confirm = btn
                        break
                except Exception:
                    continue
            if confirm is None:
                self._debug_screenshot(f"noconfirmbtn_{username}")
                return False, "no confirm button"

            confirm.click(force=True)
            self._wait(3000 / 1000)

            if self._verify_blocked():
                return True, "blocked"
            self._debug_screenshot(f"unverified_{username}")
            return False, "unverified (check manually)"
        except PWTimeout:
            self._debug_screenshot(f"timeout_{username}")
            return False, "timeout"
        except Exception as e:
            self._debug_screenshot(f"error_{username}")
            return False, f"block failed: {str(e)[:80]}"

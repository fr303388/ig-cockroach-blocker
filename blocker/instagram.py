"""Instagram 網頁版操作：搜尋帳號、判斷是否該封、執行封鎖。

v0.9 修正重點（IG 網頁版改版後重新實測）：
  * 首頁已經沒有搜尋框了（實測首頁 input 數量 = 0），搜尋改走 IG 自己的
    /web/search/topsearch/ 建議 API。回傳的是「帳號清單」，不會混到 reels。
    舊版從 /explore/search/keyword/ 抓 href，該頁全是 /reels/、/explore/、
    /popular/ 等導覽連結，抓不到真正的對方主頁，一開啟就被 IG 導去 reels 隨機影片。
  * 進入個人主頁後會再次驗證 URL 與頁面元素，確認真的停在 <username>/ 才動作。
  * 「⋯」選單改用 svg[aria-label=選項] 定位。原本用 get_by_role(button, 更多)
    會誤點到「個人簡介的更多」展開鈕（同樣是 div[role=button]），導致永遠找不到封鎖。
  * 封鎖後會驗證是否真的封鎖成功，不再無條件回報成功。
  * IG 網頁版目前已沒有「連帶封鎖未來帳號」選項，找不到時會誠實回報，不再假報成功。
"""
import re
import json
import random
import datetime
import os
import time
from typing import Tuple, List, Dict, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeout

from .config import Settings, DEBUG_DIR
from .i18n import t

# IG 帳號名規則：1~30 個英數字、底線、句點
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# 導覽列 / 系統頁面，絕不是使用者帳號
RESERVED_PATHS = {
    "reels", "reel", "explore", "stories", "story", "hashtag", "tags",
    "location", "locations", "challenge", "tv", "clips", "saved", "collections",
    "notifications", "about", "settings", "direct", "inbox", "messages",
    "activity", "emails", "challenge", "lists", "guides", "shop", "marketplace",
    "legal", "privacy", "terms", "popular", "lite", "web", "accounts",
    "login", "signup", "password", "logout", "api", "graphql", "p", "i",
    "s", "a", "b", "c", "d", "e", "f", "g", "h", "j", "k", "l", "m", "n",
    "o", "q", "r", "t", "u", "v", "w", "x", "y", "z", "developer", "abouts",
}

# 頁面上表示「這個帳號不存在 / 無法使用」的文字
PAGE_UNAVAILABLE_RE = re.compile(
    r"很抱歉|此頁面無法使用|Page Not Found|Sorry, this page|"
    r"isn.t available|內容目前無法顯示|not available",
    re.I,
)

# 粉絲數辨識
#
# 實測（2026/09）兩個平台的粉絲數結構都一樣：
#   • 數字和「位粉絲」是**兩個分開的文字節點**（"73.8萬" + "位粉絲"）
#   • 它們的**共同祖先**是 DIV 或 A（IG 是 A[href]，Threads 是 DIV[role=button]）
#   • 因此不能只找「同時含數字和粉絲的單一元素」，要往上找共同祖先。
FOLLOWERS_RE = re.compile(
    r"([\d][\d.,]*)\s*(億|万|萬|w|W|k|K|m|M)?\s*(?:位|名)?\s*(?:粉絲|追蹤者|followers)",
    re.I,
)

# 進入個人主頁後，用來確認「真的在主頁」的元素
PROFILE_MARKER = 'svg[aria-label="選項"], svg[aria-label="Options"]'

# 主頁標頭「⋯」（選項）按鈕。IG 會依介面語言給不同的 aria-label，
# 中文版是「選項」、英文版是「Options」，title 屬性也可能是。
OPTIONS_SELS = (
    'svg[aria-label="選項" i]',
    'svg[aria-label="Options" i]',
    'svg[aria-label="更多" i]',
    'svg[aria-label="More" i]',
    'svg[title="選項" i]',
    'svg[title="Options" i]',
)

# 藍勾勾認證徽章。
#
# ⚠ 舊版只寫 svg[aria-label*="verified" i]，那是**英文**介面的字串。
#   台灣使用者看到的是中文版，aria-label 是「已驗證」，所以篩選永遠失效，
#   藍勾勾帳號會被當成一般帳號送去封鎖。現在把各語言都列進來。
VERIFIED_SELS = (
    'svg[aria-label="已驗證"]',
    'svg[aria-label="已认证"]',
    'svg[aria-label="认证徽章"]',
    'svg[aria-label*="認証済み"]',
    'svg[aria-label*="認証バッジ"]',
    'svg[aria-label*="认证"]',
    'svg[aria-label*="驗證"]',
    'svg[aria-label*="verified" i]',
    'svg[aria-label*="Verified"]',
    'svg[aria-label*="blue check"]',
)

# 「封鎖」在不同介面語言下的樣子。IG 與 Threads 共用。
#
# 字元碼點對照（已用系統字型放大比對確認，請勿再改錯）：
#     U+9396 = 鎖（正體）   <- 繁中 IG / Threads 實際用這個
#     U+9392 = 鎒（錯字，不要用）
#     U+93B6 = 鎶（錯字，不要用）
#     U+9501 = 锁（簡體）
# 下面用 \u 跳脫序列寫死，避免日後編輯時被字形相近的字取代。
BLOCK_ITEM_RE = re.compile("^\\s*(\u5c01\u9396|\u5c01\u9501|Block)\\s*$")
# 封鎖後畫面上會出現的「解除封鎖」
UNBLOCK_ITEM_RE = re.compile("\u89e3\u9664\u5c01\u9396|\u89e3\u9664\u5c01\u9501|Unblock", re.I)


class _Stopped(Exception):
    """使用者按下「停止」。"""


class InstagramBlocker:
    PLATFORM = "instagram"

    def __init__(self, page: Page, s: Settings, log, already_blocked: set):
        self.page = page
        self.s = s
        self.log = log
        self.already = already_blocked
        # 關鍵字 -> API 拿到的候選資訊（username / full_name / is_verified / following）
        self._meta: Dict[str, dict] = {}
        self._own_username: Optional[str] = None
        # 目前正在處理的帳號（「點 ID 旁邊的 ⋯」備援策略要用）
        self._current_username: str = ""
        self._landing_page = "https://www.instagram.com/explore/"
        # _ready() 確認過登入後就記住，這一輪不必每個關鍵字都重新導頁
        self._ready_ok = False
        # 由 worker 注入，讓每個等待都能被「停止」打斷
        self.stop_event = None

    # ---------- 可中斷的等待 / 導見 ----------
    # 注意：Playwright 的 sync API 不是執行緒安全的，
    # 所以不能用「另一個執行緒去關 context」來中斷
    # （會單 greenlet.error）。改成在這個執行緒裡把等待
    # 切成小片段轉設 stop_event。

    def stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _wait(self, seconds: float) -> bool:
        """可被中斷的等待。回傳 False 代表使用者要求停止。"""
        end = time.time() + max(0.0, seconds)
        while True:
            if self.stopped():
                return False
            remain = end - time.time()
            if remain <= 0:
                return True
            try:
                self.page.wait_for_timeout(min(0.25, remain) * 1000)
            except Exception:
                return True

    def _goto(self, url: str, timeout: float = 30.0) -> None:
        """可被中斷的頁面導見。用短 timeout 分段圖試。"""
        deadline = time.time() + timeout
        while True:
            if self.stopped():
                raise _Stopped()
            remain = deadline - time.time()
            if remain <= 0:
                raise PWTimeout(f"開啟 {url} 逾時")
            try:
                self.page.goto(url, wait_until="domcontentloaded",
                               timeout=int(min(remain, 6.0) * 1000))
                return
            except PWTimeout:
                if self.stopped():
                    raise _Stopped()
                if time.time() >= deadline:
                    raise PWTimeout(f"開啟 {url} 逾時")
            except _Stopped:
                raise

    def _goto_profile(self, username: str, tries: int = 4) -> bool:
        """導到某人的個人主頁，並確認網址真的還停在那裡。

        導覽後反覆確認網址，不對就重導。這是對 IG 前端渲染的保險措施：
        page.goto() 只等到 domcontentloaded 就回傳，而 IG 載入後還會做一次
        client-side 導覽，偶爾會把頁面換掉。回傳 True 代表確定停在目標帳號。
        """
        target = f"https://www.instagram.com/{username}/"
        want = username.strip().strip("/").lower()
        last_url = ""
        for attempt in range(max(1, tries)):
            if self.stopped():
                return False
            try:
                self._goto(target)
            except Exception:
                return False
            # 給 IG 一點時間做 client-side 導覽，再確認
            for _ in range(4):
                if self.stopped():
                    return False
                self._wait(0.6)
                try:
                    path = urlparse(self.page.url).path.strip("/")
                except Exception:
                    path = ""
                parts = [x for x in path.split("/") if x]
                if len(parts) == 1 and parts[0].lower() == want:
                    return True
                last_url = self.page.url
            if attempt < tries - 1:
                # 網站把我踢去別的帳號了，重導一次
                self.log(f"  [debug] got redirected to {last_url[:60]}, retrying")
                self._wait(1.0)
        self.log(f"  [debug] could not stay on @{username} (last: {last_url[:60]})")
        return False

    def _wait_for(self, locator, timeout: float = 8.0) -> bool:
        """可被中斷的等待元素出現。"""
        end = time.time() + timeout
        while time.time() < end:
            if self.stopped():
                return False
            try:
                if locator.count() > 0:
                    return True
            except Exception:
                return False
            if not self._wait(0.4):
                return False
        return False

    # ---------- 除錯 ----------
    def _debug_screenshot(self, tag: str):
        if not self.s.debug_screenshots:
            return
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            ts = datetime.datetime.now().strftime("%H%M%S")
            safe = re.sub(r"[^A-Za-z0-9_.-]", "", tag)[:40]
            path = os.path.join(DEBUG_DIR, f"{safe}_{ts}.png")
            self.page.screenshot(path=path, full_page=False)
            self.log(f"  [debug] {path}")
        except Exception:
            pass

    def _ready(self) -> bool:
        """把瀏覽器帶到一個已登入、可發 API 請求的頁面。

        這一輪只要確認過一次就够了 —— 每個關鍵字都重新導頁會白白多花
        好幾秒（實測一個關鍵字要 7 秒左右），而 session 在一輪內不會無故失效。
        真的失效時，後續的搜尋與開主頁都會失敗並記錄在 log，不會被忽略。
        """
        if self._ready_ok:
            return True
        try:
            self._goto(self._landing_page, timeout=30.0)
            if not self._wait(2.5):
                return False
            if "/accounts/login" in self.page.url:
                self.log(t("r_login_expired", platform="Instagram"))
                return False
            self._ready_ok = True
            return True
        except _Stopped:
            return False
        except PWTimeout:
            self.log(t("r_load_timeout", platform="Instagram"))
            return False
        except Exception as e:
            self.log(t("r_open_fail2", platform="Instagram", error=str(e)[:120]))
            return False

    # ---------- 候選帳號驗證 ----------
    def _is_valid_username(self, username: str, keyword: str) -> bool:
        """只接受「真的有可能是使用者帳號」的字串，擋掉 reels/explore/p 等導覽路徑。"""
        if not username:
            return False
        u = username.strip().strip("/")
        if not USERNAME_RE.fullmatch(u):
            return False
        # IG 帳號不能以 . 或 _ 開頭結尾
        if u.startswith(".") or u.endswith(".") or u.startswith("_"):
            return False
        if u.strip("._") == "":
            return False
        if u.lower() in RESERVED_PATHS:
            return False
        if self._own_username and u.lower() == self._own_username.lower():
            return False
        # 只處理真的含關鍵字的帳號（IG 搜尋是模糊的，會回一堆不相關的帳號）
        if self.s.require_keyword_match and keyword:
            meta = self._meta.get(u.lower(), {})
            haystack = f"{u} {meta.get('full_name', '')}".lower()
            if keyword.lower() not in haystack:
                return False
        return True

    def _prefilter(self, users: List[dict], keyword: str) -> List[str]:
        """先用 API 回傳的欄位篩掉一部分，省下開主頁的次數（也降低被 IG 風控的機率）。"""
        out: List[str] = []
        for item in users:
            u = (item.get("username") or "").strip()
            if not u:
                continue
            key = u.lower()
            if key in self._meta:
                self._meta[key]["full_name"] = item.get("full_name", self._meta[key].get("full_name", ""))
                u = key
            if not self._is_valid_username(u, keyword):
                continue
            if u.lower() in self.already:
                continue

            verified = item.get("is_verified")
            if self.s.skip_verified and verified:
                self.log(f"  @{u}: verified, skipping (API)")
                continue
            fs = item.get("friendship_status") or {}
            if self.s.skip_following and fs.get("following"):
                self.log(f"  @{u}: you follow this, skipping (API)")
                continue
            if u not in out:
                out.append(u)
        return out

    # ---------- 搜尋 ----------
    def _api_topsearch(self, keyword: str) -> List[dict]:
        """呼叫 IG 的搜尋建議 API，回傳結構化帳號清單。"""
        return self._api_topsearch_many([keyword])

    def _api_topsearch_many(self, queries: List[str]) -> List[dict]:
        """一次並行打多個搜尋 API 查詢，回傳合併去重後的帳號清單。

        為什麼要並行：每個查詢字串單獨打要 1.9 秒左右，8 個字串循序打就是
        21 秒。改成在瀏覽器裡 Promise.all 一次發出後只要 3 秒（實測結果完全
        一樣、沒有任何 HTTP 錯誤）。連續快速打 API 確實會整批收到 HTTP 500，
        但那是在完全沒有間隔的情況下；一次 8 個並行請求實測是正常的。
        真的有問題時會退回去逐個打。
        """
        if not queries:
            return []
        users = self._api_topsearch_parallel(queries)
        if not users and len(queries) > 1:
            self.log("  [search] parallel query failed, retrying one by one")
            for q in queries:
                for item in self._api_topsearch_parallel([q]):
                    key = item.get("username", "").lower()
                    if key in self._meta and self._meta[key] is item:
                        continue
                    users.append(item)
        return users

    def _api_topsearch_parallel(self, queries: List[str]) -> List[dict]:
        """實際執行並行查詢，並把結果塞進 self._meta。"""
        try:
            replies = self.page.evaluate(
                """async (queries) => {
                    const one = async (q) => {
                        try {
                            const r = await fetch(
                                '/web/search/topsearch/?query='
                                    + encodeURIComponent(q) + '&context=blended',
                                {headers: {'X-IG-App-ID': '936619743392459',
                                           'X-Requested-With': 'XMLHttpRequest'},
                                 credentials: 'include'});
                            if (!r.ok) return {q: q, err: 'HTTP ' + r.status};
                            return {q: q, text: await r.text()};
                        } catch (e) {
                            return {q: q, err: String(e).slice(0, 60)};
                        }
                    };
                    return Promise.all(queries.map(one));
                }""",
                list(queries),
            )
        except Exception as e:
            self.log(f"  [search] API call failed: {str(e)[:80]}")
            return []
        if not replies:
            return []

        users: List[dict] = []
        seen = set()
        errors = []
        for reply in replies:
            if not isinstance(reply, dict):
                continue
            if reply.get("err"):
                errors.append("%s:%s" % (reply.get("q", "?"), reply["err"]))
                continue
            try:
                data = json.loads(reply.get("text") or "")
            except Exception:
                continue
            for entry in (data.get("users") or []):
                u = entry.get("user") or {}
                username = (u.get("username") or "").strip()
                if not username or username.lower() in seen:
                    continue
                seen.add(username.lower())
                item = {
                    "username": username,
                    "full_name": u.get("full_name") or "",
                    "is_verified": bool(u.get("is_verified")),
                    "friendship_status": u.get("friendship_status") or {},
                }
                self._meta[username.lower()] = item
                users.append(item)
        if errors:
            self.log("  [search] some queries failed: " + ", ".join(errors[:4]))
        return users

    def _read_suggestions(self) -> List[dict]:
        """讀取搜尋框下拉建議列裡的「純帳號頁」連結。

        /reels/、/explore/、/popular/ 等導航路徑會被正則排除。
        """
        rows = self.page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('a[href^="/"]').forEach(a => {
            const h = a.getAttribute('href') || '';
            if (!/^\\/[A-Za-z0-9._]{1,30}\\/?$/.test(h)) return;
            const r = a.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            out.push({href: h.replace(/\\/$/, ''),
                      txt: (a.innerText || '').replace(/\\n/g, ' ').trim()});
          });
          return out;
        }""")
        return rows or []

    def _suggest_api(self, keyword: str) -> List[dict]:
        """從 IG 原生搜尋框的下拉建議列抓帳號。

        為什麼需要這個：`/web/search/topsearch/` **永遠只回 5 筆**（實測
        2026/09，各頁面狀態、各 App-ID 都一樣，回應裡 `has_more` 雖然是 true
        但那個端點不提供分頁參數）。所以單靠 API 一輪最多只有 5 個候選，
        扣掉藍勾勾與大粉絲後常常剩 0~2 個 → 「不到 10 個就結束」。

        這裡走瀏覽器自己的搜尋框，建議列是另一條後端路徑，會給出更多結果。
        """
        try:
            self._goto("https://www.instagram.com/explore/")
            self._wait(2500 / 1000)
        except Exception as e:
            self.log(f"  [suggest] cannot open /explore/: {str(e)[:70]}")
            return []

        si = None
        for sel in [
            'input[placeholder="搜尋"]',
            'input[placeholder="Search"]',
            'input[aria-label*="搜尋"]',
            'input[aria-label*="Search"]',
            'input[type="search"]',
        ]:
            try:
                found = self.page.query_selector(sel)
            except Exception:
                found = None
            if found:
                si = found
                break
        if not si:
            self.log("  [suggest] no search box on this page")
            return []

        try:
            si.click(force=True)
            if not self._wait(0.6):
                return []
            si.fill("")
            si.type(keyword, delay=130)
            # 建議列是漸進渲染的：實測 type 完當下只有 8 筆，
            # 再等約 1.5 秒才長到 14 筆。所以要輪詢等它穩定。
            rows: List[dict] = []
            for _ in range(16):
                if not self._wait(0.4):
                    break
                try:
                    cur = self._read_suggestions()
                except Exception:
                    cur = []
                if len(cur) > len(rows):
                    rows = cur
                if len(rows) >= 5:
                    break
            if not rows:
                self.log("  [suggest] suggestion list stayed empty")
                return []
        except _Stopped:
            return []
        except Exception as e:
            self.log(f"  [suggest] typing failed: {type(e).__name__}: {str(e)[:70]}")
            return []

        users: List[dict] = []
        for row in (rows or []):
            m = re.fullmatch(r"/([A-Za-z0-9._]{1,30})", row.get("href") or "")
            if not m:
                continue
            username = m.group(1)
            if username.lower() in ("reels", "explore", "popular", "stories"):
                continue
            # API 先跑，若已有資料就沿用（is_verified 資訊比較好），
            # 但**不能因為已有就跳過** —— 那會讓建議列整個被丟掉。
            prev = self._meta.get(username.lower())
            if prev and prev.get("is_verified") is not None:
                users.append(prev)
                continue
            item = {
                "username": username,
                "full_name": (row.get("txt") or "")[:60],
                # 建議列不提供驗證/追蹤狀態，交給開主頁後判斷
                "is_verified": None,
                "friendship_status": {},
            }
            self._meta[username.lower()] = item
            users.append(item)
        self.log(f"  [suggest] {len(users)} from search box")
        return users

    def _dom_fallback(self, keyword: str) -> List[dict]:
        """備援：真的去 /explore/ 用搜尋框打字，再從下拉建議抓帳號。"""
        try:
            self._goto("https://www.instagram.com/explore/")
            self._wait(2500 / 1000)
        except Exception as e:
            self.log(f"  [search] cannot open /explore/: {str(e)[:80]}")
            return []

        si = None
        for sel in [
            'input[placeholder="搜尋"]',
            'input[placeholder="Search"]',
            'input[aria-label*="搜尋"]',
            'input[aria-label*="Search"]',
            'input[type="search"]',
        ]:
            found = self.page.query_selector(sel)
            if found:
                si = found
                self.log(f"  [fallback] found search box: {sel}")
                break
        if not si:
            self.log("  [fallback search] no search box, API only")
            return []

        try:
            si.click(force=True)
            if not self._wait(0.5):
                return []
            si.fill("")
            si.type(keyword, delay=140)
            if not self._wait(3.5):
                return []
        except _Stopped:
            return []
        except Exception as e:
            self.log(f"  [fallback] typing failed: {str(e)[:80]}")
            return []

        # 只抓「使用者名格式」且帶粉絲數的連結（建議列的特徵），
        # 導覽列的 /reels/、/explore/、/popular/ 沒有粉絲數，自然被排除。
        links = self.page.query_selector_all('a[href^="/"]')
        users: List[dict] = []
        for a in links:
            href = a.get_attribute("href") or ""
            m = re.fullmatch(r"/([A-Za-z0-9._]{1,30})/?", href)
            if not m:
                continue
            username = m.group(1)
            try:
                txt = (a.inner_text() or "").replace("\n", " ")
            except Exception:
                continue
            if "粉絲" not in txt and "follower" not in txt.lower():
                continue
            item = {
                "username": username,
                "full_name": txt[:60],
                "is_verified": None,
                "friendship_status": {},
            }
            self._meta.setdefault(username.lower(), item)
            users.append(item)
        return users

    # 一輪最多用幾種寫法去查 IG 的搜尋 API。實測 8 種就足以拿到 25 筆以上
    # 不重複的原始結果，再多只是徒增請求次數與被風控的機率。
    MAX_SEARCH_QUERIES = 8

    def _query_variants(self, keyword: str) -> List[str]:
        """產生多種搜尋寫法，用來把候選池撐大。

        為什麼需要：IG 的 `/web/search/topsearch/` **對同一個字串永遠只回 5 筆**。
        回應裡雖然帶了 `has_more: true`，但那個端點不接受任何分頁參數
        （實測 2026/09，各頁面狀態、各 App-ID 都一樣）。

        好消息是「不同字串會回完全不同的 5 筆」：

            "foodie"  -> 4foodie / 52_foodie / fourshark_foodie / foodieamber / aa__foodie
            "foodie_" -> foodie_._1 / foodies__eric.sharon / foodie_april / ...
            "_foodie" -> _foodie105 / foodieinyilan / ericlife_korea_foodie / ...
            "foodi"   -> 4foodie / 52_foodie / jc_foodidi / foodiesunny_ / foodie_yi06

        而且逐字縮短的前綴（f / fo / foo / ...）也各自給不同結果，
        累積起來遠超過 5 個。所以改用多種寫法輪詢，把不重複的帳號累積起來。
        這也讓「關鍵字字面比對」能發揮作用：不相關的結果會被 _prefilter 擋掉。
        """
        k = keyword.strip()
        out = [k]
        # IG 帳號常用底線裝飾，先試這些變體
        for v in (k + "_", "_" + k, k + "1", "a" + k):
            if v not in out:
                out.append(v)
        # 再試逐字縮短的前綴，由長到短
        for n in range(len(k) - 1, 1, -1):
            v = k[:n]
            if v not in out:
                out.append(v)
        return out

    def search_accounts(self, keyword: str) -> List[str]:
        """搜尋關鍵字，回傳值得檢查的帳號清單。"""
        keyword = keyword.strip()
        if not keyword:
            return []
        if not self._ready():
            return []

        # 單一查詢字串只有 5 筆，所以用多種寫法累積（見 _query_variants）
        variants = self._query_variants(keyword)[:self.MAX_SEARCH_QUERIES]
        merged: List[dict] = []
        seen = set()
        for item in (self._api_topsearch_many(variants) or []):
            key = (item.get("username") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
        used = len(variants)

        source = "search API"
        if not merged:
            # API 整批掛掉時才退回去走瀏覽器原生搜尋框
            self.log("  [search] API gave nothing, trying the search box")
            merged = self._suggest_api(keyword)
            source = "search box"
        if not merged:
            self.log("  [search] search box gave nothing, trying DOM fallback")
            merged = self._dom_fallback(keyword)
            source = "DOM fallback"

        if not merged:
            self._debug_screenshot(f"nosearch_{keyword}")
            self.log(f"[IG] keyword \"{keyword}\": no candidates found")
            return []

        candidates = self._prefilter(merged, keyword)
        self.log(f"[IG] \"{keyword}\" via {source} ({used} queries): "
                 f"{len(merged)} raw -> {len(candidates)} candidates: {candidates[:12]}")
        if not candidates:
            self._debug_screenshot(f"allfiltered_{keyword}")
        return candidates

    # ---------- 判斷 ----------
    def _on_profile_page(self, username: str) -> Optional[str]:
        """確認目前真的停在某人的個人主頁；回傳 None 代表沒停對。

        判斷順序：先看網址（擋掉被導去 /reels/、登入頁），再看「⋯」有沒有渲染出來。
        IG 的個人主頁是前端渲染的，網址對了但內容可能還沒生出來，所以要等。
        """
        try:
            path = urlparse(self.page.url).path.strip("/")
        except Exception:
            return None
        parts = [p for p in path.split("/") if p]
        if len(parts) != 1 or parts[0].lower() != username.lower():
            return None

        for attempt in range(2):
            try:
                self.page.locator(PROFILE_MARKER).first.wait_for(state="attached", timeout=8000)
                return parts[0]
            except PWTimeout:
                pass
            except Exception:
                return None
            # 第一次沒等到就重整再試一次
            if attempt == 0:
                try:
                    self._goto(self.page.url, timeout=20.0)
                    self._wait(2500 / 1000)
                except Exception:
                    return None

        # 網址正確、也沒有「頁面無法使用」字樣，就當作有效主頁（只是版面可能改版了）
        try:
            if PAGE_UNAVAILABLE_RE.search(self.page.inner_text("body")[:400]):
                return None
        except Exception:
            pass
        self.log(f"  @{parts[0]}: url ok but no options button, IG may have "
                 f"changed; continuing anyway")
        return parts[0]

    def _read_followers(self, retries: int = 3, wait_ms: int = 1500) -> Optional[int]:
        """讀取個人主頁標頭區的粉絲數。

        實測做法：先找到文字剛好是「位粉絲 / 追蹤者 / followers」的元素，
        然後**往上找最近的祖先**（最多 4 層），那個祖先的完整文字就是
        「73.8萬位粉絲」這種格式。兩個平台（IG / Threads）都適用。
        """
        for _ in range(max(1, retries)):
            try:
                n = self.page.evaluate("""() => {
                    const KW = /^(位|名)?\s*(粉絲|追蹤者|followers)$/i;
                    const main = document.querySelector('main') || document.body;
                    // 1) 找到「位粉絲」節點
                    let seed = null;
                    for (const el of main.querySelectorAll('*')) {
                        const own = [...el.childNodes]
                            .filter(n => n.nodeType === 3)
                            .map(n => n.textContent.trim()).join('').trim();
                        if (own && KW.test(own)) { seed = el; break; }
                    }
                    if (!seed) return null;
                    // 2) 往上找最近、且文字看起來像「數字+粉絲」的祖先
                    let el = seed;
                    for (let up = 0; up < 5 && el; up++) {
                        const t = (el.textContent || '')
                            .replace(/[\s\u00a0|｜]+/g, ' ').trim();
                        if (/^[\d][\d.,]*\s*(億|万|萬|[wWkKmM])?\s*(位|名)?\s*(粉絲|追蹤者|followers)/i.test(t)) {
                            return t.slice(0, 24);
                        }
                        el = el.parentElement;
                    }
                    return null;
                }""")
            except Exception:
                n = None
            if n:
                v = self._parse_count(n)
                if v is not None:
                    return v
            if not self._wait(wait_ms / 1000):
                return None
        return None

    @staticmethod
    def _parse_count(txt: str) -> Optional[int]:
        m = FOLLOWERS_RE.search(txt or "")
        if not m:
            return None
        try:
            n = float(m.group(1).replace(",", ""))
        except Exception:
            return None
        unit = m.group(2)
        if unit in ("萬", "万", "w", "W"):
            n *= 10_000
        elif unit == "億":
            n *= 100_000_000
        elif unit in ("k", "K"):
            n *= 1_000
        elif unit in ("m", "M"):
            n *= 1_000_000
        return int(n)

    # 追蹤狀態：只看主頁標頭「關注／追蹤」那顆按鈕
    #
    # 這裡一定要用座標範圍篩選，不能用 get_by_text：
    #   • Threads 左側選單「其他動態消息 → 追蹤中」文字完全相同，會誤判成「你已追蹤」
    #   • IG 的「1009追蹤中」是追蹤人數連結，不過它帶數字，全字比對自然不會命中
    # 真正的按鈕一定在主內容區（x > 300）、標頭操作區（y 約 150~520）。
    #
    # ⚠ 「追蹤」= 尚未追蹤、「追蹤中」= 已追蹤，兩個字差一個「中」，千萬不要搞混，
    #   也不能把「追蹤」同時放進兩張表，否則會永遠判成已追蹤。
    _FOLLOW_STATES = {
        "following": r"^(追蹤中|已追蹤|Following|Followed|關注中|已關注|關注著)$",
        "not_following": r"^(追蹤|Follow|關注|加關注|Follow Profile)$",
    }

    def _following_state(self, retries: int = 4, wait_ms: int = 1500) -> Optional[str]:
        """回傳 'following' / 'not_following' / None（讀不到）。

        個人主頁是前端渲染的，關注按鈕不一定馬上就生出來，
        所以要重試幾次，不要只量一次就說讀不到。
        """
        for _ in range(max(1, retries)):
            try:
                rows = self.page.evaluate("""() => {
                    const out = [];
                    // 注意：Threads 的追蹤按鈕是「沒有 role 的純 div」，
                    // 只抓 [role=button] 會漏掉，所以 div/span/button/a 都收。
                    document.querySelectorAll('div, span, button, a').forEach(e => {
                        const own = [...e.childNodes]
                            .filter(n => n.nodeType === 3)
                            .map(n => n.textContent.trim()).join('');
                        if (!own || own.length > 12) return;
                        const r = e.getBoundingClientRect();
                        if (r.width === 0) return;
                        out.push({t: own, x: Math.round(r.x), y: Math.round(r.y)});
                    });
                    return out;
                }""")
            except Exception:
                rows = None
            if rows:
                for b in rows:
                    t = b["t"]
                    if re.fullmatch(self._FOLLOW_STATES["following"], t):
                        state = "following"
                    elif re.fullmatch(self._FOLLOW_STATES["not_following"], t):
                        state = "not_following"
                    else:
                        continue
                    if b["x"] < 300:                 # 左側選單的同名項目
                        continue
                    if not (150 <= b["y"] <= 520):   # 不在標頭操作區
                        continue
                    return state
            if not self._wait(wait_ms / 1000):
                return None
        return None

    def inspect_and_decide(self, username: str) -> Tuple[bool, str]:
        """進入帳號頁確認是否符合封鎖條件。"""
        username = username.strip().strip("/")
        if not username:
            return False, t("r_invalid_name")
        if username.lower() in self.already:
            return False, t("r_already_blocked")
        self._current_username = username

        if not self._goto_profile(username):
            if self.stopped():
                return False, t("r_stopped")
            return False, t("r_wrong_page", url=self.page.url[:60])
        if not self._wait(random.uniform(1.5, 2.5)):
            return False, t("r_stopped")

        # 帳號不存在
        try:
            head = self.page.inner_text("body")[:600]
        except Exception:
            head = ""
        if PAGE_UNAVAILABLE_RE.search(head):
            return False, t("r_not_exist")

        # 真的停在對方主頁？（擋掉被導去 /reels/ 或登入頁）
        landed = self._on_profile_page(username)
        if landed is None:
            self._debug_screenshot(f"wrongpage_{username}")
            return False, t("r_wrong_page", url=self.page.url[:70])
        username = landed

        # 自己主頁
        try:
            self_btn = self.page.get_by_text(
                re.compile(r"編輯個人檔案|編輯個人簡介|Edit profile", re.I))
            if self_btn.count() > 0:
                self._own_username = username
                return False, t("r_own_profile")
        except Exception:
            pass

        # 已封過
        try:
            unblock = self.page.get_by_text(re.compile(r"解除封鎖|Unblock", re.I))
            if unblock.count() > 0:
                return False, t("r_ig_blocked")
        except Exception:
            pass

        # 藍勾勾（API 先篩過，這裡再確認一次，避免 API 欄位缺漏）
        # 注意：必須涵蓋中文／日文介面的字串，否則中文版 IG 完全篩不到。
        if self.s.skip_verified:
            try:
                for sel in VERIFIED_SELS:
                    if self.page.query_selector(sel):
                        return False, t("r_verified")
            except Exception:
                pass

        # 已追蹤（用 _following_state，避開側邊欄同名元素）
        if self.s.skip_following:
            state = self._following_state()
            if state == "following":
                return False, t("r_following")
            if state is None:
                self.log(t("r_no_follow", user=username))

        # 粉絲數門檻
        fans = self._read_followers()
        if fans is None:
            self.log(t("r_no_fans", user=username))
        elif fans >= self.s.skip_follower_gt:
            return False, t("r_fans_over", fans=f"{fans:,}", limit=f"{self.s.skip_follower_gt:,}")

        return True, (t("r_ok_with_fans", fans=f"{fans:,}") if fans else t("r_ok"))

    # ---------- 執行封鎖 ----------
    #
    # 「⋯」選單的開法改成分層嘗試（v0.10 修正）。
    #
    # ⚠ 為什麼不能只看「有沒有跳出 dialog」當成功訊號：
    #   IG 頁面上還有別的 dialog（限時動態列、推廣、登入提示…），
    #   它們裡面的 div[role=button] 會被舊條件數到，於是程式誤以為選單開了，
    #   結果後面根本找不到「封鎖」→ 回報無法封鎖。
    #   現在的成功條件收緊成：**容器裡真的有「封鎖 / Block」這個項目**。
    #
    # 另外舊版只等固定 1.8 秒、只試一次。IG 與 Threads 同時跑時兩個 Chromium
    #   搶 CPU，選單常常還沒渲染完就被判定成「找不到」。
    #   現在改成：多策略 → 輪詢最多 6 秒 → 整組重試 3 次。

    def _tag_options_menu(self) -> bool:
        """在瀏覽器裡**一次**找出「⋯ 選單」容器並打上 data-cb-menu 標記。

        回傳是否找到。用單次 evaluate 有兩個好處：
          1. 快 —— 舊版要跑十幾次 locator 往返，IG 忙碌時單次就能好幾秒。
          2. 不會有「檢查時在、取值時跑掉」的競態。
        """
        try:
            return bool(self.page.evaluate("""(reSrc) => {
              const re = new RegExp(reSrc);
              const cands = document.querySelectorAll(
                  '[role="menu"], div[role="dialog"]');
              for (const el of cands) {
                const items = el.querySelectorAll(
                    'div[role="button"], button, [role="menuitem"]');
                let hit = false;
                for (const b of items) {
                  if (re.test((b.innerText || '').trim())) { hit = true; break; }
                }
                if (hit) {
                  document.querySelectorAll('[data-cb-menu]').forEach(
                      n => n.removeAttribute('data-cb-menu'));
                  el.setAttribute('data-cb-menu', '1');
                  return true;
                }
              }
              return false;
            }""", BLOCK_ITEM_RE.pattern))
        except Exception:
            return False

    def _options_menu_root(self):
        """找出「⋯ 選單」這個容器；找不到回 None。

        只接受「裡面真的有『封鎖 / Block』項目」的容器，
        這樣頁面上其他 dialog（限時動態列、推廣…）不會被誤認成選單。
        """
        if not self._tag_options_menu():
            return None
        try:
            loc = self.page.locator('[data-cb-menu="1"]')
            return loc if loc.count() else None
        except Exception:
            return None

    def _wait_menu_open(self, timeout: float = 6.0) -> bool:
        """輪詢等「⋯ 選單（含封鎖項目）」出現。用 _wait 切片，按「停止」仍可中斷。"""
        end = time.time() + timeout
        while True:
            if self._tag_options_menu():
                return True
            if time.time() >= end:
                return False
            if not self._wait(0.2):
                return False

    def _dismiss_menu(self) -> None:
        """把可能半開的選單關掉，避免干擾下一次嘗試。"""
        try:
            if self._tag_options_menu():
                self.page.keyboard.press("Escape")
                self._wait(0.4)
                if self._tag_options_menu():
                    # 有些版本不吃 ESC，改用滑鼠點空白處
                    self.page.mouse.click(40, 400)
                    self._wait(0.4)
        except Exception:
            pass
        # 不論關掉沒有，都把標記清掉，避免殘留標記造成誤判。
        # （下一次 _tag_options_menu() 會重新搜尋並重新標記）
        try:
            self.page.evaluate(
                "() => document.querySelectorAll('[data-cb-menu]')"
                ".forEach(n => n.removeAttribute('data-cb-menu'))")
        except Exception:
            pass

    def _header_username_box(self, username: str) -> Optional[dict]:
        """主頁標頭「使用者名稱」的座標框。

        拿不到就回 None。這個座標是「點 ID 旁邊的 ⋯」備援策略用的。
        """
        try:
            return self.page.evaluate("""(uname) => {
                const main = document.querySelector('main') || document.body;
                const want = (uname || '').toLowerCase();
                let best = null;
                main.querySelectorAll('span, a, div, h1, h2').forEach(e => {
                    const own = [...e.childNodes]
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim()).join('').trim()
                        .toLowerCase();
                    if (!own || own !== want) return;
                    const r = e.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return;
                    if (r.y > 200) return;                 // 標頭在上面
                    if (!best || r.width < best.width) best = r;   // 取最貼文字的那層
                });
                if (!best) return null;
                return {x: best.x, y: best.y, w: best.width, h: best.height,
                        cx: best.x + best.width / 2, cy: best.y + best.height / 2};
            }""", (username or "").lower())
        except Exception:
            return None

    def _options_button_boxes(self) -> list:
        """所有看起來像標頭「⋯」的按鈕座標（已依位置排序，最像的排前面）。"""
        boxes = []
        for sel in OPTIONS_SELS:
            try:
                loc = self.page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    el = loc.nth(i)
                    box = el.bounding_box()
                    if not box:
                        continue
                    # 點的是可點的祖先按鈕，座標要往上擴到按鈕本身
                    tgt = el.locator("xpath=ancestor-or-self::*[@role='button'][1]")
                    if tgt.count() > 0:
                        b2 = tgt.first.bounding_box()
                        if b2:
                            box = b2
                    if box["y"] > 320:        # 排除貼文區的圖示
                        continue
                    if box["x"] < 380:        # 排除左側導覽列
                        continue
                    boxes.append(box)
                except Exception:
                    continue
        # 由左到右（⋯ 通常緊接在 ID 右邊）排序，取第一個當主要目標
        boxes.sort(key=lambda b: b["x"])
        return boxes

    def _open_options_menu(self) -> bool:
        """點開個人主頁 ID 旁邊的「⋯」選單。

        回傳 True 代表選單已經開好、裡面有可點的項目。
        """
        for attempt in range(3):
            if self.stopped():
                return False
            self._dismiss_menu()

            # --- 策略 A：鍵盤（點 ID 上方 → Tab → 空白鍵）---
            # 使用者手動實測最穩的一招：不受藍勾勾徽章、帳號名長短、
            # 視窗寬度影響。放最前面當主力。
            if attempt == 0 and self._open_menu_via_keyboard():
                return True

            # --- 策略 B：用 aria-label 定位「⋯」按鈕 ---
            for sel in OPTIONS_SELS:
                if self.stopped():
                    return False
                try:
                    loc = self.page.locator(sel)
                    n = loc.count()
                except Exception:
                    continue
                for i in range(n):
                    try:
                        el = loc.nth(i)
                        box = el.bounding_box()
                        if not box or box["y"] > 320 or box["x"] < 380:
                            continue
                        tgt = el.locator("xpath=ancestor-or-self::*[@role='button'][1]")
                        if tgt.count() == 0:
                            tgt = el
                        try:
                            tgt.first.scroll_into_view_if_needed(timeout=1500)
                        except Exception:
                            pass
                        tgt.first.click(force=True)
                        if self._wait_menu_open(6.0):
                            return True
                        self._dismiss_menu()
                    except Exception:
                        continue

            # --- 策略 C：依序試所有候選座標 ---
            for box in self._options_button_boxes():
                if self.stopped():
                    return False
                try:
                    self.page.mouse.click(
                        box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    if self._wait_menu_open(4.0):
                        return True
                    self._dismiss_menu()
                except Exception:
                    continue

            # --- 策略 E：座標偏移（點 ID 右側幾個位置）---
            # 有藍色勾勾認證徽章時，徽章會把 ⋯ 往右推，所以要試幾個偏移量。
            ub = self._header_username_box(self._current_username or "")
            if ub:
                for dx in (16, 30, 44, 8, 60):
                    try:
                        self.page.mouse.click(ub["x"] + ub["w"] + dx, ub["cy"])
                        if self._wait_menu_open(3.0):
                            return True
                        self._dismiss_menu()
                    except Exception:
                        continue

            # --- 策略 E：鍵盤（第二次嘗試時再用一次）---
            if attempt > 0 and self._open_menu_via_keyboard():
                return True

            self._wait(0.8)

        self.log("  [debug] options menu: all strategies failed")
        return False

    def _focus_is_options_button(self) -> bool:
        """目前鍵盤焦點是不是「⋯」選項按鈕。"""
        try:
            return bool(self.page.evaluate("""() => {
              const a = document.activeElement;
              if (!a || a === document.body) return false;
              const sel = 'svg[aria-label="\u9078\u9805"], '
                        + 'svg[aria-label="Options"]';
              if (a.querySelector && a.querySelector(sel)) return true;
              const p = a.closest('div[role="button"], button, [tabindex]');
              return !!(p && p.querySelector(sel));
            }"""))
        except Exception:
            return False

    def _open_menu_via_keyboard(self, max_tab: int = 10) -> bool:
        """點一下 ID 上方讓頁面取得焦點，再用 Tab 走到「⋯」、空白鍵開選單。

        這是使用者手動測出來最穩的手法，實測有效：
            點 ID 上方 → Tab ×2 → 空白鍵
        Tab 1 會停在「使用者名稱連結」，Tab 2 就停在旁邊的「⋯」。

        為什麼要用這招：座標會隨視窗寬度、藍勾勾認證徽章、帳號名長短而移動，
        但 Tab 的**順序**不受這些影響。而且按「⋯」一定要用**空白鍵**，
        用 Enter 沒有反應。
        """
        ub = self._header_username_box(self._current_username or "")
        points = []
        if ub:
            # 點在 ID **上方**一點，避免點到 ID 本身觸發連結跳走
            points.append((ub["cx"], max(2.0, ub["y"] - 14)))
        points.append((30, max(2.0, (ub["y"] - 14) if ub else 20)))

        for (cx, cy) in points:
            if self.stopped():
                return False
            try:
                self.page.mouse.click(cx, cy)
                self._wait(0.4)
                for _ in range(max_tab):
                    if self.stopped():
                        return False
                    self.page.keyboard.press("Tab")
                    self._wait(0.12)
                    if not self._focus_is_options_button():
                        continue
                    # 焦點對了，按**空白鍵**（Enter 對這個按鈕沒用）
                    self.page.keyboard.press("Space")
                    if self._wait_menu_open(4.0):
                        return True
                    self.page.keyboard.press("Space")     # 有些情況要再按一次
                    if self._wait_menu_open(3.0):
                        return True
                    self._dismiss_menu()
                    return False
            except Exception:
                continue
        return False

    def _find_confirm_dialog(self, timeout: float = 6.0):
        """找出「封鎖確認」彈窗：同時含有「封鎖」與「取消」的那個 dialog。

        原本只掃一次就放棄；現在會輪詢等待，避免 IG 渲染慢時誤判。
        """
        cancel_re = re.compile(r"^\s*(取消|Cancel)\s*$")
        block_re = BLOCK_ITEM_RE
        end = time.time() + timeout
        while True:
            n = 0
            try:
                n = self.page.locator('div[role="dialog"]').count()
            except Exception:
                n = 0
            for i in range(n):
                try:
                    d = self.page.locator('div[role="dialog"]').nth(i)
                    if d.get_by_text(cancel_re).count() > 0 \
                            and d.get_by_text(block_re).count() > 0:
                        return d
                except Exception:
                    continue
            if time.time() >= end:
                return None
            if not self._wait(0.3):
                return None

    def _menu_items(self) -> list:
        """回傳「⋯ 選單」裡所有可點項目的 (locator, 文字)。

        找不到真正的選單容器就回空清單 —— 絕不拿頁面上其他 dialog 的按鈕
        （例如限時動態列）來當選單項目。
        """
        if not self._tag_options_menu():
            return []
        try:
            root = self.page.locator('[data-cb-menu="1"]')
        except Exception:
            return []
        out = []
        for sel in ('div[role="button"]', 'button', '[role="menuitem"]', 'li'):
            try:
                loc = root.locator(sel)
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                try:
                    txt = (loc.nth(i).inner_text() or "").strip()
                except Exception:
                    continue
                if txt:
                    out.append((loc.nth(i), txt))
            if out:
                break        # 找到一組就夠，不要混到別的容器
        return out

    def block(self, username: str) -> Tuple[bool, str]:
        """在目前帳號頁執行封鎖，並驗證結果。"""
        if self.s.dry_run:
            return True, t("dryrun_banner")
        self._current_username = username

        try:
            if not self._on_profile_page(username):
                self._debug_screenshot(f"notprofile_{username}")
                return False, t("r_wrong_page", url=self.page.url[:60])

            if not self._open_options_menu():
                self._debug_screenshot(f"nomenu_{username}")
                return False, "menu not found"

            # 1. 點選單裡的「封鎖」（要精確比對整個文字，
            #    否則「封鎖並檢舉」「封鎖此帳號」會一起被命中）
            picked = None
            for _ in range(2):                      # 沒點到就重開一次選單
                items = self._menu_items()
                picked = None
                for el, txt in items:
                    if BLOCK_ITEM_RE.fullmatch(txt):
                        picked = el
                        break
                if picked is not None:
                    break
                self.log("  [debug] menu items = " + repr([x[1] for x in items])[:200])
                if not self._open_options_menu():
                    break
            if picked is None:
                self._debug_screenshot(f"noblock_{username}")
                return False, "no block item"

            picked.click(force=True)
            self._wait(2500 / 1000)

            # 2. 找確認彈窗（含「取消」按鈕的那個）
            dialog = self._find_confirm_dialog()
            if dialog is None:
                self._debug_screenshot(f"noconfirm_{username}")
                return False, "no confirm dialog"

            # 3. 連帶封鎖未來帳號（IG 網頁版目前已移除此選項）
            future_note = ""
            if self.s.block_future_accounts:
                try:
                    opt = dialog.get_by_text(
                        re.compile(r"未來建立|future accounts|other accounts they have|其他帳號"))
                    if opt.count() > 0:
                        opt.first.click(force=True)
                        self._wait(700 / 1000)
                        self.log("  selected: also block future accounts")
                        future_note = " (+future accounts)"
                    else:
                        future_note = " (web app has no future-accounts option)"
                except Exception:
                    future_note = " (web app has no future-accounts option)"

            # 4. 按確認（彈窗內的「封鎖」，不是取消）
            #    先找 <button> 再找 div[role=button]：彈窗標題是「封鎖xxx？」，
            #    用 <button> 可以避免誤抓到包住整個彈窗的容器。
            confirm = None
            for sel in ('button', 'div[role="button"]'):
                try:
                    loc = dialog.locator(sel)
                    n = loc.count()
                except Exception:
                    continue
                for i in range(n):
                    try:
                        btn = loc.nth(i)
                        if BLOCK_ITEM_RE.fullmatch(
                                        (btn.inner_text() or "").strip()):
                            confirm = btn
                            break
                    except Exception:
                        continue
                if confirm is not None:
                    break
            if confirm is None:
                self._debug_screenshot(f"noconfirmbtn_{username}")
                return False, "no Block button in the confirm dialog"

            confirm.click(force=True)

            # 5. 驗證是否真的封鎖成功（IG 更新畫面需要時間，改成輪詢）
            for _ in range(16):
                if self._verify_blocked():
                    return True, f"blocked{future_note}"
                if not self._wait(0.5):
                    break
            self._debug_screenshot(f"unverified_{username}")
            return False, "unverified (check manually)"

        except PWTimeout:
            self._debug_screenshot(f"timeout_{username}")
            return False, "timeout"
        except Exception as e:
            self._debug_screenshot(f"error_{username}")
            return False, f"block failed: {str(e)[:80]}"

    def _verify_blocked(self) -> bool:
        """封鎖後畫面上應該出現「解除封鎖」，否則視為未成功。"""
        try:
            if self.page.get_by_text(re.compile(r"解除封鎖|Unblock", re.I)).count() > 0:
                return True
        except Exception:
            pass
        try:
            # 有些情況會跳 toast，例如「已封鎖 @xxx」
            if self.page.get_by_text(re.compile(r"已封鎖|You blocked|Blocked @", re.I)).count() > 0:
                return True
        except Exception:
            pass
        return False

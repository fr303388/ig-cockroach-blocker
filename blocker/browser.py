"""Playwright 瀏覽器封裝：登入流程、載入儲存狀態。"""
import os
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from .config import IG_STATE_PATH, THREADS_STATE_PATH


def ensure_chromium() -> None:
    """首次執行時自動下載 Chromium。已存在則略過。"""
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return
    except Exception:
        pass
    import subprocess, sys
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    except Exception:
        try:
            subprocess.run(["playwright", "install", "chromium"], check=False)
        except Exception:
            pass


def state_path_for(platform: str) -> str:
    """登入狀態檔路徑。正確定義在 blocker.config（不需要 import playwright），
    這裡保留一份轉發，因為早期版本這個函式是放在 browser.py 的。"""
    from .config import state_path_for as _impl
    return _impl(platform)


def start_browser(headless: bool = False):
    """啟動瀏覽器，回傳 (playwright, browser)。用完要記得 stop。"""
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    return pw, browser


def open_logged_in_context(browser: Browser, platform: str) -> BrowserContext:
    """用已儲存的登入狀態開 context。沒登入會丟 FileNotFoundError。"""
    sp = state_path_for(platform)
    if not os.path.exists(sp):
        raise FileNotFoundError(f"尚未登入 {platform}，請先在主視窗按「登入{platform}」")
    return browser.new_context(storage_state=sp, viewport={"width": 1280, "height": 800})


def interactive_login(platform: str, is_done) -> None:
    """開可見瀏覽器讓使用者手動登入，is_done() 回傳 True 後存 state。

    is_done: GUI 端傳入的函式，回傳 True 代表使用者按了「完成登入」。
    """
    sp = state_path_for(platform)
    # Threads 已搬到 threads.com，舊網域會 301 轉址
    url = ("https://www.instagram.com/" if platform == "instagram"
           else "https://www.threads.com/")

    pw, browser = start_browser(headless=False)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.goto(url)
    # 輪詢等使用者完成。
    # 注意：用 try 包起來，因為使用者如果自己把瀏覽器視窗關掉，
    # 這裡的 page.wait_for_timeout 會直接拋 "Target page/… has been closed"，
    # 那時就要明確回報「登入未完成」，不能假裝存檔成功。
    try:
        while not is_done():
            page.wait_for_timeout(500)
    except Exception:
        raise RuntimeError(
            "登入視窗被關閉了，還沒存成登入狀態。"
            "請重新按登入按鈕，讓瀏覽器視窗保持開著，"
            "登入完成後再按主程式的「完成登入」。")
    ctx.storage_state(path=sp)
    ctx.close()
    browser.close()
    pw.stop()

"""設定讀寫：所有使用者可調參數集中在這裡。"""
import json
import os
from dataclasses import dataclass, asdict, field
from typing import List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
BLOCKED_LOG_PATH = os.path.join(BASE_DIR, "blocked_log.csv")
IG_STATE_PATH = os.path.join(BASE_DIR, "ig_state.json")
THREADS_STATE_PATH = os.path.join(BASE_DIR, "threads_state.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug")
# 介面語言偏好（空白 = 跟隨系統）
LANG_PATH = os.path.join(BASE_DIR, "lang.txt")


def state_path_for(platform: str) -> str:
    """登入狀態檔路徑。

    主要定義在 blocker/browser（要 import playwright），
    這裡保留一份不依賴 playwright 的實作，讓單純讀設定的程式
    （例如 GUI 或測試）不必載入瀏覽器套件。
    """
    return IG_STATE_PATH if platform == "instagram" else THREADS_STATE_PATH


@dataclass
class Settings:
    # 要封鎖的關鍵字（帳號名稱含任一字串就列入觀察名單）
    keywords: List[str] = field(default_factory=lambda: ["foodie"])
    # 每輪最多封鎖數（保守值，避免觸發 IG 風控）
    max_per_run: int = 10
    # 每輪兩個平台合計最多實際觸發幾個候選帳號。
    # 這是「上限」的安全網：當對象沒有被真的封鎖時
    # （例如 Threads 改版找不到選單），max_per_run 永遠不會滿，
    # 程式會把幾十上百個候選全跑完，看起來就是無限持續。
    # 0 = 自動推測（至少 max_per_run 的 3 倍，不少於 30）
    max_try_per_run: int = 60
    # 每日排程時間，格式 "HH:MM"，可多筆
    schedule_times: List[str] = field(default_factory=lambda: ["12:00", "22:00"])
    # 只處理「使用者名或顯示名確實含關鍵字」的帳號。
    # IG 搜尋是模糊的，關掉這個會連不相關的帳號一起進來。
    require_keyword_match: bool = True
    # 過濾規則
    skip_verified: bool = True       # 跳過藍勾勾
    skip_following: bool = True      # 跳過你已追蹤的
    skip_follower_gt: int = 5000     # 粉絲數高於此就跳過（視為正常 KOL）
    # 封鎖前固定等待秒數（GUI 用單一數字控制，min=max 就是固定秒數）
    delay_min: float = 6.0
    delay_max: float = 6.0
    # 平台開關
    run_instagram: bool = True
    run_threads: bool = True
    # 封鎖時是否勾選「連帶封鎖此帳號未來建立的其他帳號」
    # 注意：IG 網頁版目前已移除這個選項，此設定只在 IG 重新加回來時才有效。
    block_future_accounts: bool = True
    # 演練模式：只搜尋 + 判斷 + 列出會封誰，完全不點封鎖。
    # 預設 True —— 新使用者第一次開程式時不該直接對真人帳號下封鎖，
    # 要真的封鎖得自己進設定取消勾選，這道防呆不該留給舊設定檔。
    dry_run: bool = True
    # 存檔除錯截圖到 debug 資料夾
    debug_screenshots: bool = True


def load() -> Settings:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 過濾掉舊版或新版多出來的欄位，避免 Settings(**data) 直接爆掉
            valid = {f.name for f in Settings.__dataclass_fields__.values()}
            data = {k: v for k, v in data.items() if k in valid}
            return Settings(**data)
        except Exception:
            return Settings()
    return Settings()


def save(s: Settings) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, ensure_ascii=False, indent=2)

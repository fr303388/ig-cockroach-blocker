"""多語系：繁體中文 / 簡體中文 / 日本語 / English。

用法：
    from .i18n import t, set_lang, detect_system_lang, LANGS
    set_lang("en")
    print(t("skip", user="abc", reason="..."))

設計重點：
  * 第一次啟動會**跟著作業系統語言**選語言，之後記住使用者的選擇。
  * 未來新增語言只要在 STRINGS 加一個 key，不會漏翻（缺 key 會顯示 ⚠ 而不是崩潰）。
  * 執行緒安全的全域語言切換（只用一個字串變數，切換成本極低）。
"""
import os
import threading
from typing import Dict, Optional

from .config import LANG_PATH

SUPPORTED = ("zh_TW", "zh_CN", "ja", "en")

# 語言顯示名稱（用該語言自己寫，避免看不懂）
LANG_NAMES = {
    "zh_TW": "繁體中文",
    "zh_CN": "簡體中文",
    "ja": "日本語",
    "en": "English",
}

STRINGS: Dict[str, Dict[str, str]] = {
    # ==================== 繁體中文 ====================
    "zh_TW": {
        "app_title": "蟑螂封鎖器 — IG / Threads 關鍵字帳號定期封鎖",
        "tab_settings": "① 設定",
        "tab_log": "② 執行紀錄",
        "tab_about": "③ 關於 / 免責",
        "lang_label": "語言",
        "lang_follow_system": "跟隨系統",

        "lbl_keywords": "要封鎖的關鍵字（逗號分隔，帳號名稱含任一字串就列入觀察）：",
        "lbl_max": "每輪最多封鎖幾個（保守建議 10）：",
        "lbl_max_try": "每輪最多實際觸發幾個帳號（填此表上限，未實際封鎖時會被限制）：",
        "lbl_delay": "每個帳號封鎖前等待幾秒（降低被 IG 風控的機率）：",
        "lbl_schedule": "每日執行時間（每行一個，格式 HH:MM，24小時制）：",
        "chk_verified": "跳過藍勾勾認證帳號（正常 KOL）",
        "chk_following": "跳過你已追蹤的帳號",
        "lbl_fans": "粉絲人數高於設定數量視為正常帳號不封鎖",
        "chk_future": "封鎖時連帶封鎖此帳號未來開的新帳號（IG 網頁版目前已無此選項）",
        "chk_require_kw": "只處理「使用者名或顯示名確實含關鍵字」的帳號（建議保持勾選）",
        "chk_dryrun": "⚠ 演練模式：只搜尋與判斷、列出會封誰，完全不點封鎖（第一次請先開）",
        "chk_debug": "失敗時自動存除錯截圖到 debug 資料夾",
        "chk_ig": "也處理 Instagram",
        "chk_th": "也處理 Threads",

        "btn_login_ig": "① 登入 Instagram",
        "btn_login_th": "② 登入 Threads",
        "btn_run_now": "立即跑一輪",
        "btn_start": "開始排程",
        "btn_stop": "停止",
        "status_ready": "就緒",
        "status_running": "執行中…",
        "status_stopping": "停止中…",

        "login_title": "登入 {platform}",
        "login_hint": "請在剛剛跳出的瀏覽器視窗登入 {platform}\n完成後按下面按鈕",
        "login_done_btn": "完成登入",
        "login_saving": "{platform} 正在儲存登入狀態…",
        "login_ok": "✅ {platform} 登入完成，狀態已儲存",
        "login_not_saved": "❌ {platform} 登入狀態沒有存成，請重新登入",
        "login_failed": "{platform} 登入未完成：{error}",
        "login_err_title": "{platform} 登入啟動失敗",
        "login_err_body": "錯誤：{error}\n\n常見原因：\n"
                          "1. 你在登入完成前就把瀏覽器視窗關掉了\n"
                          "   → 請讓視窗保持開著，確認已登入後再按「完成登入」\n"
                          "2. Chromium 未安裝 → 命令列跑  python -m playwright install chromium\n"
                          "3. 防毒軟體攔截了 Chromium\n"
                          "4. 視窗被擋在背景 → 檢查工作列\n\n完整 stack 已寫入 crash.log",
        "login_in_progress": "登入中",

        "busy_title": "忙碌中",
        "busy_body": "已有任務在執行，請先停止",
        "confirm_title": "即將真的封鎖帳號",
        "confirm_body": "演練模式目前是關閉的，這一輪會「真的」封鎖帳號。\n\n"
                        "關鍵字：{kw}\n本輪上限：{mx}\n粉絲門檻：{fans}\n\n"
                        "確定要執行嗎？\n（建議先勾「演練模式」跑一次確認結果）",
        "cancelled": "已取消",
        "manual_run": "手動觸發：立即跑一輪",
        "schedule_on": "排程已啟動",
        "stopping": "收到停止指令，正在中斷瀏覽器操作…",

        "font_chiron": "字型：{family}（含中/半粗字重）",
        "font_fallback": "字型：{family}（沒找到 Chiron GoRound TC，已用系統字型）",

        # 執行紀錄
        "dryrun_banner": "⚠ 演練模式：只會搜尋與判斷，不會真的封鎖任何人",
        "parallel_note": "▶ Instagram 與 Threads 同時並行執行",
        "no_platform": "！Instagram 與 Threads 都沒有勾選，沒有事情要做",
        "start_platform": "==== 開始處理 {name} ====",   # 已含 ====，worker 不再包
        "browser_fail": "！{name} 開啟瀏覽器失敗：{error}",
        "search_kw": "搜尋關鍵字：{kw}",
        "search_fail": "搜尋失敗：{error}",
        "no_candidate": "  （關鍵字「{kw}」沒有可檢查的候選）",
        "check_fail": "  @{user} 檢查失敗：{error}",
        "skip": "  @{user}：略過（{reason}）",
        "dryrun_hit": "  🔍 @{user}：{reason}　→ 演練模式，不執行封鎖",
        "blocking": "  @{user}：{reason}，執行封鎖…",
        "waiting": "  等待 {sec:.0f} 秒…",
        "block_error": "  ❌ @{user} 封鎖時發生錯誤：{error}",
        "blocked_ok": "  ✅ @{user} {msg}",
        "blocked_fail": "  ❌ @{user} {msg}",
        "platform_crash": "！{name} 發生未預期的錯誤：{error}",
        "round_stopped": "==== 已停止（本輪實際封鎖 {n} 個）====",
        "round_dry_end": "==== 演練結束：實際封鎖 0 個（上方列出的是「會被封鎖」的帳號）====",
        "round_end": "==== 本輪結束：共封鎖 {n} 個帳號　{detail} ====",
        "sched_ready": "排程就緒，下次執行 {h} 小時 {m} 分鐘後",
        "round_crash": "本輪異常中斷：{error}",
        "round_crash": "本輪異常中斷：{error}",
        "tried_summary": "  本輪實際觸發 {tried} 個（IG {ig} / Threads {th}），上限 {cap} 個",
        "round_crash": "本輪異常中斷：{error}",
        "try_limit_hit": "  ⚠ 已達到本輪可實際觸發的上限（{n} 個），本輪到此結束",

        # blocker 內部訊息（reason 欄位會用到）
        "r_already_blocked": "已封過",
        "r_invalid_name": "無效帳號名",
        "r_open_timeout": "開啟帳號頁逾時",
        "r_open_fail": "開啟帳號頁失敗",
        "r_stopped": "已停止",
        "r_not_exist": "帳號不存在或已停用",
        "r_wrong_page": "沒停在對方主頁（目前網址：{url}）",
        "r_not_logged_in": "未登入 Threads",
        "r_own_profile": "這是你自己的主頁，跳過",
        "r_ig_blocked": "IG 端已封過",
        "r_th_blocked": "Threads 端已封過",
        "r_verified": "藍勾勾跳過",
        "r_following": "你已追蹤，跳過",
        "r_fans_over": "粉絲 {fans} 超過門檻 {limit}，跳過",
        "r_ok_with_fans": "符合條件（粉絲 {fans}）",
        "r_ok": "符合條件",
        "r_no_fans": "  @{user}：讀不到粉絲數，仍往下判斷",
        "r_no_follow": "  @{user}：讀不到追蹤狀態，仍往下判斷",
        "r_login_expired": "！登入狀態失效，請重新按「登入 {platform}」",
        "r_load_timeout": "！載入 {platform} 逾時",
        "r_open_fail2": "！開啟 {platform} 失敗：{error}",

        "about_text": (
            "用途：定期在 Instagram / Threads 網頁版，自動封鎖名稱含指定關鍵字的騷擾帳號。\n\n"
            "使用步驟：\n"
            "  1. 到「設定」分頁，輸入關鍵字、排程時間\n"
            "  2. 保持勾選「演練模式」，先按「立即跑一輪」看它會列出哪些帳號\n"
            "  3. 確認名單沒問題後，再取消勾選演練模式\n"
            "  4. 按「登入 Instagram」「登入 Threads」，在跳出的瀏覽器視窗手動登入一次\n"
            "  5. 回到本視窗的小跳出視窗按「完成登入」\n"
            "  6. 按「開始排程」，程式會在你設定的時間自動跑\n\n"
            "免責聲明：\n"
            "  • 本工具僅供個人對抗跟騷、洗版、垃圾訊息之用。\n"
            "  • 自動化操作可能違反 Meta 服務條款，有帳號被限制或停權風險。\n"
            "  • 請勿用於大規模封鎖正常使用者或壓縮特定個人言論。\n"
            "  • 使用本工具造成的任何後果由您自行承擔。"
        ),
    },

    # ==================== 简体中文 ====================
    "zh_CN": {
        "app_title": "蟑螂封锁器 — IG / Threads 关键字账号定期封锁",
        "tab_settings": "① 设置",
        "tab_log": "② 执行记录",
        "tab_about": "③ 关于 / 免责",
        "lang_label": "语言",
        "lang_follow_system": "跟随系统",

        "lbl_keywords": "要封锁的关键字（逗号分隔，账号名称含任一字符串就列入观察）：",
        "lbl_max": "每轮最多封锁几个（保守建议 10）：",
        "lbl_max_try": "每轮最多实际触發几个账号（填此表上限，未实际封锁时会被限制）：",
        "lbl_delay": "每个账号封锁前等待几秒（降低被 IG 风控的概率）：",
        "lbl_schedule": "每日执行时间（每行一个，格式 HH:MM，24 小时制）：",
        "chk_verified": "跳过蓝勾认证账号（正常 KOL）",
        "chk_following": "跳过你已关注的账号",
        "lbl_fans": "粉丝人数高于设定数量视为正常账号不封锁",
        "chk_future": "封锁时连带封锁此账号未来开的新账号（IG 网页版目前已无此选项）",
        "chk_require_kw": "只处理「用户名或显示名确实含关键字」的账号（建议保持勾选）",
        "chk_dryrun": "⚠ 演练模式：只搜索与判断、列出会封谁，完全不点封锁（第一次请先开）",
        "chk_debug": "失败时自动存调试截图到 debug 文件夹",
        "chk_ig": "也处理 Instagram",
        "chk_th": "也处理 Threads",

        "btn_login_ig": "① 登录 Instagram",
        "btn_login_th": "② 登录 Threads",
        "btn_run_now": "立即跑一轮",
        "btn_start": "开始排程",
        "btn_stop": "停止",
        "status_ready": "就绪",
        "status_running": "执行中…",
        "status_stopping": "停止中…",

        "login_title": "登录 {platform}",
        "login_hint": "请在刚刚跳出的浏览器窗口登录 {platform}\n完成后按下面按钮",
        "login_done_btn": "完成登录",
        "login_saving": "{platform} 正在保存登录状态…",
        "login_ok": "✅ {platform} 登录完成，状态已保存",
        "login_not_saved": "❌ {platform} 登录状态没有保存成功，请重新登录",
        "login_failed": "{platform} 登录未完成：{error}",
        "login_err_title": "{platform} 登录启动失败",
        "login_err_body": "错误：{error}\n\n常见原因：\n"
                          "1. 你在登录完成前就把浏览器窗口关掉了\n"
                          "   → 请让窗口保持开着，确认已登录后再按「完成登录」\n"
                          "2. Chromium 未安装 → 命令行跑  python -m playwright install chromium\n"
                          "3. 杀毒软件拦截了 Chromium\n"
                          "4. 窗口被挡在背景 → 检查任务管理器\n\n完整 stack 已写入 crash.log",
        "login_in_progress": "登录中",

        "busy_title": "忙碌中",
        "busy_body": "已有任务在执行，请先停止",
        "confirm_title": "即将真的封锁账号",
        "confirm_body": "演练模式目前是关闭的，这一轮会「真的」封锁账号。\n\n"
                        "关键字：{kw}\n本轮上限：{mx}\n粉丝门槛：{fans}\n\n"
                        "确定要执行吗？\n（建议先勾「演练模式」跑一次确认结果）",
        "cancelled": "已取消",
        "manual_run": "手动触發：立即跑一轮",
        "schedule_on": "排程已启动",
        "stopping": "收到停止指令，正在中断浏览器操作…",

        "font_chiron": "字体：{family}（含中/半粗字重）",
        "font_fallback": "字体：{family}（没找到 Chiron GoRound TC，已用系统字体）",

        "dryrun_banner": "⚠ 演练模式：只搜索与判断，不会真的封锁任何人",
        "parallel_note": "▶ Instagram 与 Threads 同时并行执行",
        "no_platform": "！Instagram 与 Threads 都没勾选，没有事情要做",
        "start_platform": "==== 开始处理 {name} ====",
        "browser_fail": "！{name} 打开浏览器失败：{error}",
        "search_kw": "搜索关键字：{kw}",
        "search_fail": "搜索失败：{error}",
        "no_candidate": "  （关键字「{kw}」没有可检查的候选）",
        "check_fail": "  @{user} 检查失败：{error}",
        "skip": "  @{user}：跳过（{reason}）",
        "dryrun_hit": "  🔍 @{user}：{reason}　→ 演练模式，不执行封锁",
        "blocking": "  @{user}：{reason}，执行封锁…",
        "waiting": "  等待 {sec:.0f} 秒…",
        "block_error": "  ❌ @{user} 封锁时发生错误：{error}",
        "blocked_ok": "  ✅ @{user} {msg}",
        "blocked_fail": "  ❌ @{user} {msg}",
        "platform_crash": "！{name} 发生未预期的错误：{error}",
        "round_stopped": "==== 已停止（本轮实际封锁 {n} 个）====",
        "round_dry_end": "==== 演练结束：实际封锁 0 个（上面列出的是「会被封锁」的账号）====",
        "round_end": "==== 本轮结束：共封锁 {n} 个账号　{detail} ====",
        "sched_ready": "排程就绪，下次执行 {h} 小时 {m} 分钟后",
        "round_crash": "本轮异常中断：{error}",
        "round_crash": "本轮异常中断：{error}",
        "tried_summary": "  本轮实际触發 {tried} 个（IG {ig} / Threads {th}），上限 {cap} 个",
        "round_crash": "本轮异常中断：{error}",
        "try_limit_hit": "  ⚠ 已达到本轮可实际触發的上限（{n} 个），本轮到此结束",

        "r_already_blocked": "已封过",
        "r_invalid_name": "无效账号名",
        "r_open_timeout": "打开账号页超时",
        "r_open_fail": "打开账号页失败",
        "r_stopped": "已停止",
        "r_not_exist": "账号不存在或已停用",
        "r_wrong_page": "没停在对方主页（当前网址：{url}）",
        "r_not_logged_in": "未登录 Threads",
        "r_own_profile": "这是你自己的主页，跳过",
        "r_ig_blocked": "IG 端已封过",
        "r_th_blocked": "Threads 端已封过",
        "r_verified": "蓝勾跳过",
        "r_following": "你已关注，跳过",
        "r_fans_over": "粉丝 {fans} 超过门槛 {limit}，跳过",
        "r_ok_with_fans": "符合条件（粉丝 {fans}）",
        "r_ok": "符合条件",
        "r_no_fans": "  @{user}：读不到粉丝数，仍往下判断",
        "r_no_follow": "  @{user}：读不到关注状态，仍往下判断",
        "r_login_expired": "！登录状态失效，请重新按「登录 {platform}」",
        "r_load_timeout": "！加载 {platform} 超时",
        "r_open_fail2": "！打开 {platform} 失败：{error}",

        "about_text": (
            "用途：定期在 Instagram / Threads 网页版，自动封锁名称含指定关键字的骚扰账号。\n\n"
            "使用步骤：\n"
            "  1. 到「设置」分页，输入关键字、排程时间\n"
            "  2. 保持勾选「演练模式」，先按「立即跑一轮」看它会列出哪些账号\n"
            "  3. 确认名单没问题后，再取消勾选演练模式\n"
            "  4. 按「登录 Instagram」「登录 Threads」，在跳出的浏览器窗口手动登录一次\n"
            "  5. 回到本窗口的小弹出窗口按「完成登录」\n"
            "  6. 按「开始排程」，程序会在你设定的时间自动跑\n\n"
            "免责声明：\n"
            "  • 本工具仅供个人对抗跟骚、洗版、垃圾讯息之用。\n"
            "  • 自动化操作可能违反 Meta 服务条款，有账号被限制或停权风险。\n"
            "  • 请勿用于大规模封锁正常用户或压缩特定个人言论。\n"
            "  • 使用本工具造成的任何后果由您自行承担。"
        ),
    },

    # ==================== 日本語 ====================
    "ja": {
        "app_title": "ゴキ専用ブロッカー — IG / Threads キーワードアカウント定期ブロック",
        "tab_settings": "① 設定",
        "tab_log": "② 実行ログ",
        "tab_about": "③ 概要 / 免責",
        "lang_label": "言語",
        "lang_follow_system": "システムに従う",

        "lbl_keywords": "ブロックするキーワード（カンマ区切り。名前に含む文字列を候補にします）：",
        "lbl_max": "1 回のブロック上限（控えめなら 10）：",
        "lbl_max_try": "トライ実際チェック上限：",
        "lbl_delay": "各アカウントのブロック前に待つ秒数（IG のリスク回避）：",
        "lbl_schedule": "毎日の実行時刻（1 行に 1 つ、形式 HH:MM、24 時間制）：",
        "chk_verified": "認証済みアカウント（正当な KOL）をスキップ",
        "chk_following": "フォロー済みのアカウントをスキップ",
        "lbl_fans": "この数以上のフォロワーは通常のアカウントとしてブロックしない",
        "chk_future": "今後そのアカウントが作る新アカウントもブロック（IG ウェブ版には現在この項目なし）",
        "chk_require_kw": "「ユーザー名または表示名にキーワードが含まれる」アカウントだけ処理（推奨：オン）",
        "chk_dryrun": "⚠ 演習モード：判定のみ。ブロック対象を一覧表示するだけ（初回必須）",
        "chk_debug": "失敗時に debug フォルダへスクリーンショットを保存",
        "chk_ig": "Instagram も処理する",
        "chk_th": "Threads も処理する",

        "btn_login_ig": "① Instagram にログイン",
        "btn_login_th": "② Threads にログイン",
        "btn_run_now": "今すぐ実行",
        "btn_start": "スケジュール開始",
        "btn_stop": "停止",
        "status_ready": "待機中",
        "status_running": "実行中…",
        "status_stopping": "停止中…",

        "login_title": "{platform} にログイン",
        "login_hint": "今開いたブラウザウィンドウで {platform} にログインしてください\n完了したら下のボタンを押してください",
        "login_done_btn": "ログイン完了",
        "login_saving": "{platform} のログイン状態を保存中…",
        "login_ok": "✅ {platform} のログイン状態を保存しました",
        "login_not_saved": "❌ {platform} のログイン状態を保存できませんでした。再度ログインしてください",
        "login_failed": "{platform} ログインが未完了です：{error}",
        "login_err_title": "{platform} ログインの起動に失敗",
        "login_err_body": "エラー：{error}\n\n考えられる原因：\n"
                          "1. ログインが終わる前にブラウザウィンドウを閉じてしまった\n"
                          "   → ウィンドウを開いたまま、ログイン後に「ログイン完了」を押してください\n"
                          "2. Chromium 未インストール → コマンドプロンプトで  python -m playwright install chromium\n"
                          "3. ウイルス対策ソフトが Chromium をブロックした\n"
                          "4. ウィンドウが背後にある → タスクマネージャー確認\n\n完全な stack は crash.log に書かれました",
        "login_in_progress": "ログイン中",

        "busy_title": "処理中",
        "busy_body": "別のタスクが実行中です。先に停止してください",
        "confirm_title": "実際にブロックします",
        "confirm_body": "演習モードが無効になっています。この回は実際にブロックします。\n\n"
                        "キーワード：{kw}\n上限：{mx}\nフォロワーしきい値：{fans}\n\n"
                        "実行しますか？\n（「演習モード」で一度確認することをおすすめします）",
        "cancelled": "キャンセルしました",
        "manual_run": "手動実行：今すぐ 1 回",
        "schedule_on": "スケジュールを開始しました",
        "stopping": "停止コマンドを受け付けました。ブラウザ操作を中断しています…",

        "font_chiron": "フォント：{family}（中間/準太字重 込み）",
        "font_fallback": "フォント：{family}（Chiron GoRound TC がないためシステムフォントを使用）",

        "dryrun_banner": "⚠ 演習モード：検索と判定のみ。実際にブロックはしません",
        "parallel_note": "▶ Instagram と Threads を同時に並行実行します",
        "no_platform": "！Instagram も Threads もチェックが入っていないので、処理対象がありません",
        "start_platform": "==== {name} の処理を開始 ====",
        "browser_fail": "！{name} のブラウザ起動に失敗：{error}",
        "search_kw": "キーワードを検索：{kw}",
        "search_fail": "検索に失敗：{error}",
        "no_candidate": "  （キーワード「{kw}」に該当する候補はありません）",
        "check_fail": "  @{user} の判定に失敗：{error}",
        "skip": "  @{user}：スキップ（{reason}）",
        "dryrun_hit": "  🔍 @{user}：{reason}　→ 演習モードのためブロックしません",
        "blocking": "  @{user}：{reason}、ブロックを実行…",
        "waiting": "  {sec:.0f} 秒待機…",
        "block_error": "  ❌ @{user} のブロック中にエラー：{error}",
        "blocked_ok": "  ✅ @{user} {msg}",
        "blocked_fail": "  ❌ @{user} {msg}",
        "platform_crash": "！{name} で予期しないエラー：{error}",
        "round_stopped": "==== 停止しました（今回の実ブロック {n} 件）====",
        "round_dry_end": "==== 演習終了：実ブロック 0 件（上に挙げたのはブロックされる予定のアカウントです）====",
        "round_end": "==== 今回の実行終了：ブロック {n} 件　{detail} ====",
        "sched_ready": "スケジュール準備完了、次回実行まで {h} 時間 {m} 分",
        "round_crash": "実行が異常終了しました：{error}",
        "round_crash": "実行が異常終了しました：{error}",
        "tried_summary": "  本回の実際トライ {tried} 件（IG {ig} / Threads {th}）、上限 {cap} 件",
        "round_crash": "実行が異常終了しました：{error}",
        "try_limit_hit": "  ⚠ 本回の実際トライ上限（{n} 件）に達しました。これまで終了します",

        "r_already_blocked": "ブロック済み",
        "r_invalid_name": "無効なアカウント名",
        "r_open_timeout": "アカウントページの読み込みがタイムアウト",
        "r_open_fail": "アカウントページを開けませんでした",
        "r_stopped": "停止しました",
        "r_not_exist": "アカウントが存在しないか無効です",
        "r_wrong_page": "相手のプロフィールに移動できていません（現在：{url}）",
        "r_not_logged_in": "Threads にログインしていません",
        "r_own_profile": "これは自分のプロフィールです。スキップします",
        "r_ig_blocked": "IG 側でブロック済み",
        "r_th_blocked": "Threads 側でブロック済み",
        "r_verified": "認証済みなのでスキップ",
        "r_following": "フォロー済みなのでスキップ",
        "r_fans_over": "フォロワー {fans} はしきい値 {limit} を超えるためスキップ",
        "r_ok_with_fans": "条件に該当（フォロワー {fans}）",
        "r_ok": "条件に該当",
        "r_no_fans": "  @{user}：フォロワー数を取得できません。判定を続けます",
        "r_no_follow": "  @{user}：フォロー状態を取得できません。判定を続けます",
        "r_login_expired": "！ログイン状態が失効しました。「{platform} にログイン」を押してください",
        "r_load_timeout": "！{platform} の読み込みがタイムアウト",
        "r_open_fail2": "！{platform} を開けませんでした：{error}",

        "about_text": (
            "用途：Instagram / Threads のウェブ版で、キーワードを含むアカウントを定期的にブロックします。\n\n"
            "使い方：\n"
            "  1. 「設定」タブでキーワードと実行時刻を入力\n"
            "  2. 「演習モード」をオンにしたまま「今すぐ 1 回実行」でブロック対象を確認\n"
            "  3. リストが問題なければ演習モードをオフにする\n"
            "  4. 「Instagram にログイン」「Threads にログイン」を押し、ブラウザでログイン\n"
            "  5. 元のウィンドウの「ログイン完了」を押す\n"
            "  6. 「スケジュール開始」で設定した時刻に自動実行\n\n"
            "免責：\n"
            "  • ストーカーやスパムへの対処など、個人の防御目的のみに使用してください。\n"
            "  • 自動化は Meta の利用規約に抵触する可能性があります。アカウントが制限される恐れがあります。\n"
            "  • 正常なユーザーまとめてのブロックや表現の制限には使用しないでください。\n"
            "  • 本ツール使用の結果についてはご自身で責任を負ってください。"
        ),
    },

    # ==================== English ====================
    "en": {
        "app_title": "Cockroach Blocker — Scheduled blocking for IG / Threads",
        "tab_settings": "① Settings",
        "tab_log": "② Run log",
        "tab_about": "③ About / Disclaimer",
        "lang_label": "Language",
        "lang_follow_system": "Follow system",

        "lbl_keywords": "Keywords to block (comma separated; any account whose name contains these is reviewed):",
        "lbl_max": "Max blocks per run (10 is conservative):",
        "lbl_max_try": "Max accounts to check per run (safety cap when blocking keeps failing):",
        "lbl_delay": "Seconds to wait before each block (lowers IG risk detection):",
        "lbl_schedule": "Daily run times (one per line, HH:MM, 24-hour):",
        "chk_verified": "Skip verified accounts (legit KOLs)",
        "chk_following": "Skip accounts you already follow",
        "lbl_fans": "Accounts above this follower count are treated as normal and left alone",
        "chk_future": "Also block future accounts they create (not available on the IG web app anymore)",
        "chk_require_kw": "Only process accounts whose username or display name really contains a keyword (recommended)",
        "chk_dryrun": "⚠ Dry run: search and evaluate only, lists who would be blocked, never blocks (keep on first time)",
        "chk_debug": "Save debug screenshots to the debug folder on failure",
        "chk_ig": "Also process Instagram",
        "chk_th": "Also process Threads",

        "btn_login_ig": "① Log in Instagram",
        "btn_login_th": "② Log in Threads",
        "btn_run_now": "Run once now",
        "btn_start": "Start schedule",
        "btn_stop": "Stop",
        "status_ready": "Ready",
        "status_running": "Running…",
        "status_stopping": "Stopping…",

        "login_title": "Log in {platform}",
        "login_hint": "Log in to {platform} in the browser window that just opened\nthen press the button below",
        "login_done_btn": "Log-in complete",
        "login_saving": "Saving {platform} login state…",
        "login_ok": "✅ {platform} logged in, state saved",
        "login_not_saved": "❌ {platform} login state was not saved, please log in again",
        "login_failed": "{platform} login not completed: {error}",
        "login_err_title": "{platform} login failed to start",
        "login_err_body": "Error: {error}\n\nCommon causes:\n"
                          "1. You closed the browser window before finishing the login\n"
                          "   → keep the window open, then press \"Log-in complete\"\n"
                          "2. Chromium missing → run  python -m playwright install chromium\n"
                          "3. Antivirus blocked Chromium\n"
                          "4. Window is behind others → check the taskbar\n\nFull stack written to crash.log",
        "login_in_progress": "Logging in",

        "busy_title": "Busy",
        "busy_body": "A task is already running, stop it first",
        "confirm_title": "About to really block accounts",
        "confirm_body": "Dry run is OFF, so this run WILL block accounts.\n\n"
                        "Keywords: {kw}\nMax per run: {mx}\nFollower threshold: {fans}\n\n"
                        "Continue?\n(Recommended: run once in dry run first to check the list)",
        "cancelled": "Cancelled",
        "manual_run": "Manual trigger: running once now",
        "schedule_on": "Schedule started",
        "stopping": "Stop received, interrupting browser operations…",

        "font_chiron": "Font: {family} (with medium/semibold weights)",
        "font_fallback": "Font: {family} (Chiron GoRound TC not found, using system font)",

        "dryrun_banner": "⚠ Dry run: search and evaluate only, no one will actually be blocked",
        "parallel_note": "▶ Instagram and Threads run at the same time, in parallel",
        "no_platform": "！Neither Instagram nor Threads is selected, nothing to do",
        "start_platform": "==== Starting {name} ====",
        "browser_fail": "！Could not start browser for {name}: {error}",
        "search_kw": "Searching keyword: {kw}",
        "search_fail": "Search failed: {error}",
        "no_candidate": "  (no candidates to check for \"{kw}\")",
        "check_fail": "  @{user} check failed: {error}",
        "skip": "  @{user}: skipped ({reason})",
        "dryrun_hit": "  🔍 @{user}: {reason}　→ dry run, not blocking",
        "blocking": "  @{user}: {reason}, blocking…",
        "waiting": "  waiting {sec:.0f}s…",
        "block_error": "  ❌ @{user} error while blocking: {error}",
        "blocked_ok": "  ✅ @{user} {msg}",
        "blocked_fail": "  ❌ @{user} {msg}",
        "platform_crash": "！Unexpected error on {name}: {error}",
        "round_stopped": "==== Stopped ({n} actually blocked this run) ====",
        "round_dry_end": "==== Dry run finished: 0 actually blocked (the list above is who WOULD be blocked) ====",
        "round_end": "==== Run finished: {n} accounts blocked　{detail} ====",
        "sched_ready": "Schedule ready, next run in {h}h {m}m",
        "round_crash": "Run aborted by error: {error}",
        "round_crash": "Run aborted by error: {error}",
        "tried_summary": "  attempted {tried} accounts this run (IG {ig} / Threads {th}), cap {cap}",
        "round_crash": "Run aborted by error: {error}",
        "try_limit_hit": "  ⚠ reached this run's attempt cap ({n}), stopping here",

        "r_already_blocked": "already blocked",
        "r_invalid_name": "invalid account name",
        "r_open_timeout": "timed out opening the profile",
        "r_open_fail": "could not open the profile",
        "r_stopped": "stopped",
        "r_not_exist": "account does not exist or is disabled",
        "r_wrong_page": "not on the target profile (current url: {url})",
        "r_not_logged_in": "not logged in to Threads",
        "r_own_profile": "this is your own profile, skipping",
        "r_ig_blocked": "already blocked on IG",
        "r_th_blocked": "already blocked on Threads",
        "r_verified": "verified account, skipping",
        "r_following": "you already follow this account, skipping",
        "r_fans_over": "{fans} followers is over the threshold {limit}, skipping",
        "r_ok_with_fans": "matches criteria ({fans} followers)",
        "r_ok": "matches criteria",
        "r_no_fans": "  @{user}: could not read follower count, continuing anyway",
        "r_no_follow": "  @{user}: could not read follow state, continuing anyway",
        "r_login_expired": "！Login state expired, press \"Log in {platform}\" again",
        "r_load_timeout": "！Timed out loading {platform}",
        "r_open_fail2": "！Could not open {platform}: {error}",

        "about_text": (
            "Purpose: periodically block harassment accounts on the Instagram and Threads\n"
            "web apps whose names contain your keywords.\n\n"
            "How to use:\n"
            "  1. Open the Settings tab and enter keywords and run times\n"
            "  2. Keep Dry run ON and press \"Run once now\" to see who would be blocked\n"
            "  3. If the list looks right, turn Dry run off\n"
            "  4. Press \"Log in Instagram\" / \"Log in Threads\" and sign in once\n"
            "  5. Back in the main window, press \"Log-in complete\"\n"
            "  6. Press \"Start schedule\" to run automatically at your chosen times\n\n"
            "Disclaimer:\n"
            "  • Intended only for personal defence against stalking, spam and harassment.\n"
            "  • Automation may violate Meta's terms and get your account restricted.\n"
            "  • Do not use it to mass-block normal users or to suppress speech.\n"
            "  • You are responsible for any consequences of using this tool."
        ),
    },
}

_lock = threading.Lock()
_current: str = "zh_TW"


def detect_system_lang() -> str:
    """從作業系統語言猜測要顯示哪一種。"""
    for env in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(env, "")
        if not v:
            continue
        v = v.lower()
        if v.startswith("zh"):
            # 簡體地區（cn / sg / my）→ 簡中；其餘 → 繁中
            for tag in ("_cn", "_sg", "_my", "_hans"):
                if tag in v:
                    return "zh_CN"
            if "_tw" in v or "_hk" in v or "_hant" in v:
                return "zh_TW"
            return "zh_TW"
        if v.startswith("ja"):
            return "ja"
        if v.startswith("en"):
            return "en"
    # Windows 備援：從 UI 語言清單猜
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if lid == 0x0404:      # zh-TW
            return "zh_TW"
        if lid == 0x0804:      # zh-CN
            return "zh_CN"
        if lid == 0x0411:      # ja-JP
            return "ja"
    except Exception:
        pass
    return "zh_TW"


def load_preference() -> Optional[str]:
    """讀出上次選的語言；沒有就回 None（代表跟隨系統）。"""
    try:
        if os.path.exists(LANG_PATH):
            with open(LANG_PATH, "r", encoding="utf-8") as f:
                v = f.read().strip()
            return v if v in SUPPORTED else None
    except Exception:
        pass
    return None


def save_preference(lang: str) -> None:
    try:
        with open(LANG_PATH, "w", encoding="utf-8") as f:
            f.write(lang)
    except Exception:
        pass


def set_lang(lang: str) -> None:
    """切換語言。傳 "auto" 表示跟隨系統。"""
    global _current
    if lang == "auto" or lang not in SUPPORTED:
        lang = detect_system_lang()
    with _lock:
        _current = lang if lang in SUPPORTED else "zh_TW"


def get_lang() -> str:
    return _current


def is_following_system() -> bool:
    return load_preference() is None


def t(key: str, **kw) -> str:
    """取字串。缺 key 不會崩潰，會顯示 ⚠ 方便找出漏翻的地方。"""
    table = STRINGS.get(_current) or STRINGS["zh_TW"]
    s = table.get(key)
    if s is None:
        s = STRINGS["zh_TW"].get(key)
    if s is None:
        return f"⚠[{key}]"
    try:
        return s.format(**kw) if kw else s
    except (KeyError, IndexError, ValueError):
        return s


# 啟動時先載入偏好（沒偏好就跟系統）
set_lang(load_preference() or "auto")

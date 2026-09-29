"""背景工作：跑一輪封鎖、排程迴圈。GUI 在背景執行緒呼叫 run_round()。

v0.10：Instagram 與 Threads 改成**同時並行**執行（原本是一個跑完才跑下一個）。
"""
import csv
import os
import time
import datetime
import threading
from typing import Callable, Set, List

from .config import Settings, BLOCKED_LOG_PATH
from .browser import start_browser, open_logged_in_context
from .instagram import InstagramBlocker
from .threads import ThreadsBlocker
from .i18n import t


def load_blocked_log() -> Set[str]:
    """讀已封鎖清單。統一轉小寫，因為 IG 帳號名大小寫不敏感。"""
    seen = set()
    if os.path.exists(BLOCKED_LOG_PATH):
        with open(BLOCKED_LOG_PATH, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row:
                    seen.add(row[0].strip().lower())
    return seen


def _append_locked(username: str, platform: str, note: str, lock: threading.Lock) -> None:
    """寫入已封鎖清單。加鎖避免兩個平台同時寫壞 CSV。"""
    with lock:
        new_file = not os.path.exists(BLOCKED_LOG_PATH)
        try:
            with open(BLOCKED_LOG_PATH, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["username", "platform", "time", "note"])
                w.writerow([username, platform,
                            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note])
        except Exception:
            pass


def append_blocked(username: str, platform: str, note: str) -> None:
    _append_locked(username, platform, note, threading.Lock())


def _run_one_platform(s: Settings, platform: str, log: Callable[[str], None],
                      stop_event, already: Set[str], results: dict,
                      write_lock: threading.Lock) -> None:
    """跑單一平台（IG 或 Threads）的完整流程。"""
    tag = platform.upper()
    # 每個平台的執行緒要有自己的瀏覽器（Playwright sync API 只能綁一個執行緒）
    pw = browser = ctx = None
    blocked_here = 0
    stopped = False
    dry = getattr(s, "dry_run", False)
    # 嘗試上限由 run_round 算好放在 results 裡（兩個平台共用同一個額度）
    max_tried = int(results.get("max_tried") or 0)

    try:
        if stop_event.is_set():
            return
        log(t("start_platform", name=tag))

        try:
            pw, browser = start_browser(headless=False)
        except Exception as e:
            log(t("browser_fail", name=tag, error=str(e)[:120]))
            return

        try:
            ctx = open_logged_in_context(browser, platform)
        except FileNotFoundError as e:
            log(f"！{e}")
            results[platform] = 0
            return
        except Exception as e:
            log(t("browser_fail", name=tag, error=str(e)[:120]))
            results[platform] = 0
            return

        page = ctx.new_page()
        BlockerCls = InstagramBlocker if platform == "instagram" else ThreadsBlocker
        blocker = BlockerCls(page, s, log, already)
        # 讓 blocker 的每個等待都能被「停止」打斷
        # （Playwright sync API 非執行緒安全，不能從別的執行緒去關 context）
        blocker.stop_event = stop_event
        seen_this_round = set()

        for kw in s.keywords:
            if results["total"] >= s.max_per_run:
                break
            if stop_event.is_set():
                stopped = True
                break
            kw = kw.strip()
            if not kw:
                continue
            log(f"-- {t('search_kw', kw=kw)} --")
            try:
                candidates = blocker.search_accounts(kw)
            except Exception as e:
                if stop_event.is_set():
                    stopped = True
                    break
                log(t("search_fail", error=str(e)[:120]))
                continue

            if not candidates:
                log(t("no_candidate", kw=kw))
                continue

            for username in candidates:
                if results["total"] >= s.max_per_run:
                    break
                if results["tried"] >= max_tried:
                    log(t("try_limit_hit", n=results["tried"]))
                    stopped = False
                    break
                if stop_event.is_set():
                    stopped = True
                    break
                uname = (username or "").strip().strip("/").lower()
                if not uname or uname in seen_this_round:
                    continue
                seen_this_round.add(uname)
                # 不管後續成不成功，這裡都算一次嘗試
                results["tried"] += 1
                results["tried_" + platform] += 1

                try:
                    ok, reason = blocker.inspect_and_decide(uname)
                except Exception as e:
                    if stop_event.is_set():
                        stopped = True
                        break
                    log(t("check_fail", user=uname, error=str(e)[:100]))
                    continue
                if not ok:
                    log(t("skip", user=uname, reason=reason))
                    continue

                if dry:
                    log(t("dryrun_hit", user=uname, reason=reason))
                    continue

                log(t("blocking", user=uname, reason=reason))
                # 固定等待（GUI 的秒數；min=max 就是等固定秒數）
                delay = max(float(s.delay_min), float(s.delay_max))
                if delay > 0:
                    log(t("waiting", sec=delay))
                    waited = 0.0
                    while waited < delay:
                        if stop_event.is_set():
                            stopped = True
                            break
                        nap = min(0.5, delay - waited)
                        time.sleep(nap)
                        waited += nap
                    if stopped:
                        break

                try:
                    success, msg = blocker.block(uname)
                except Exception as e:
                    if stop_event.is_set():
                        stopped = True
                        break
                    log(t("block_error", user=uname, error=str(e)[:100]))
                    continue
                if success:
                    log(t("blocked_ok", user=uname, msg=msg))
                    already.add(uname)
                    _append_locked(uname, platform, msg, write_lock)
                    blocked_here += 1
                    results["total"] += 1
                else:
                    log(t("blocked_fail", user=uname, msg=msg))

            if stopped or results["total"] >= s.max_per_run:
                break
            if results["tried"] >= max_tried:
                break

    except Exception as e:
        log(t("platform_crash", name=tag, error=str(e)[:150]))
    finally:
        results[platform] = blocked_here
        for closer in (ctx, browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def run_round(s: Settings, log: Callable[[str], None], stop_event) -> None:
    """跑完整一輪：勾選的平台**同時並行**執行。"""
    already = load_blocked_log()
    dry = getattr(s, "dry_run", False)
    stopped = False
    write_lock = threading.Lock()
    results = {"total": 0, "instagram": 0, "threads": 0,
               "tried": 0, "tried_threads": 0, "tried_instagram": 0}
    # 嘗試上限：每個平台最多try 幾個候選。
    # 沒有這個的話，只要 block() 一直失敗（例如 Threads 改版找不到選單），
    # max_per_run 永遠不會滿，程式會把幾十上百個候選全跑完，看起來像當機。
    try:
        max_tried = int(getattr(s, "max_try_per_run", 0) or 0)
    except Exception:
        max_tried = 0
    if max_tried <= 0:
        # 沒設定就自動推導：至少要有封鎖上限的 3 倍，不然失敗時會失控
        max_tried = max(30, int(s.max_per_run) * 3)
    results["max_tried"] = max_tried

    if dry:
        log(t("dryrun_banner"))

    platforms = [p for p, on in (("instagram", s.run_instagram),
                                 ("threads", s.run_threads)) if on]

    if not platforms:
        log(t("no_platform"))
        return

    if len(platforms) > 1:
        log(t("parallel_note"))

    threads = []
    for platform in platforms:
        th = threading.Thread(
            target=_run_one_platform,
            args=(s, platform, log, stop_event, already, results, write_lock),
            daemon=True, name=f"run-{platform}")
        th.start()
        threads.append(th)

    # 主執行緒在這裡等；任一平台出例外都不會中斷其他平台
    for th in threads:
        while th.is_alive():
            th.join(timeout=0.3)
            if stop_event.is_set():
                stopped = True

    total = results["total"]
    if stopped:
        log(t("round_stopped", n=total))
    elif dry:
        log(t("round_dry_end"))
    else:
        parts = [f"{k.upper()} {v}" for k, v in results.items()
                 if k in ("instagram", "threads") and v]
        log(t("round_end", n=total, detail=" / ".join(parts)))
    log(t("tried_summary",
          tried=results["tried"],
          ig=results["tried_instagram"],
          th=results["tried_threads"],
          cap=results.get("max_tried", 0)))


def seconds_until_next_run(s: Settings) -> float:
    """計算距離下一個排程時間的秒數。"""
    now = datetime.datetime.now()
    candidates = []
    for t_ in s.schedule_times:
        t_ = t_.strip()
        if not t_:
            continue
        try:
            h, m = [int(x) for x in t_.split(":")]
        except Exception:
            continue
        next_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if next_dt <= now:
            next_dt += datetime.timedelta(days=1)
        candidates.append(next_dt)
    if not candidates:
        return 3600
    return (min(candidates) - now).total_seconds()


def scheduler_loop(s: Settings, log: Callable[[str], None], stop_event) -> None:
    """排程迴圈：睡醒到下個排程時間就跑一輪。"""
    while not stop_event.is_set():
        wait_s = seconds_until_next_run(s)
        h = int(wait_s // 3600)
        m = int((wait_s % 3600) // 60)
        log(t("sched_ready", h=h, m=m))
        slept = 0
        while slept < wait_s and not stop_event.is_set():
            time.sleep(1)
            slept += 1
        if stop_event.is_set():
            return
        from .config import load
        s = load()
        try:
            run_round(s, log, stop_event)
        except Exception as e:
            log(t("round_crash", error=str(e)[:150]))

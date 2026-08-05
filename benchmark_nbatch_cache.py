"""
benchmark_nbatch_cache.py
=========================
實測 n_batch 調高 與 Prompt Caching (keep_alive) 的實際效能差異。

量測指標：
  - 首字延遲 (TTFT): 從發出請求到收到第一個 token 的時間
  - 總延遲 (Total):  完整回覆所需時間
  - 快取命中 (Cache): Ollama 回報的 prompt_eval_count vs prompt_eval_duration

執行方式：
  cd <project_root>
  C:\\Users\\corps\\anaconda3\\envs\\Daily\\python.exe benchmark_nbatch_cache.py
"""

import asyncio
import json
import os
import sys
import time

import aiohttp

# ─── 設定 ─────────────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"

# 使用 QUICK_MODEL (ollama backend)：因為 PRIMARY_MODEL 走 LMS，not Ollama
# 改成你實際有跑的 Ollama 模型
MODEL = "krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS"

# 模擬雪莉的 System Prompt（中長度，約 400 tokens，測快取效果最明顯）
SYSTEM_PROMPT = """\
你是橘雪莉，一個 15 歲活潑可愛的台灣女高中生，同時自稱是名偵探。
你說話風格生動活潑，喜歡用台灣年輕人的口氣，偶爾夾雜台灣俗語和流行語。
你有強烈的好奇心，對各種謎題和推理遊戲充滿熱情。
你很愛吃甜食，特別是珍珠奶茶和麻薯。
你的口頭禪是「哼，這個謎題難不倒本偵探！」以及「笨蛋笨蛋～」。
你雖然傲嬌但其實很在乎朋友，遇到大家困難的時候會積極提供幫助。
你擅長邏輯推理、資料分析，以及找出隱藏的線索。
現在你正在 Discord 的聊天群組裡，與熟悉的群友輕鬆互動閒聊。
請用繁體中文回覆，語氣要自然、活潑、像個真實的台灣高中生。
"""

# 短 user prompt，讓差異集中在 prefill 階段（System Prompt 處理速度）
USER_PROMPT = "嗨雪莉！今天有沒有什麼有趣的事情發生啊？"

# ─── 輔助函式 ──────────────────────────────────────────────────────────────────

async def do_request(
    session: aiohttp.ClientSession,
    *,
    keep_alive: str,
    num_batch: int,
    label: str,
) -> dict:
    """Send a single /api/chat request and return timing + Ollama metrics."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "stream": False,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": 8192,
            "num_predict": 80,          # 限制輸出長度以加快測試速度
            "num_batch": num_batch,
            "temperature": 0.7,
        },
    }

    t_start = time.perf_counter()
    async with session.post(f"{OLLAMA_URL}/api/chat", json=payload) as resp:
        resp.raise_for_status()
        data = await resp.json()
    t_end = time.perf_counter()

    total_ms = (t_end - t_start) * 1000

    # Ollama 回報的內部計時（ns → ms）
    prompt_eval_count = data.get("prompt_eval_count", 0)       # System+User tokens 數量
    prompt_eval_dur_ms = data.get("prompt_eval_duration", 0) / 1_000_000   # Prefill 時間
    eval_count = data.get("eval_count", 0)                     # 生成 token 數量
    eval_dur_ms = data.get("eval_duration", 0) / 1_000_000     # Decode 時間

    prompt_tps = (prompt_eval_count / (prompt_eval_dur_ms / 1000)) if prompt_eval_dur_ms > 0 else 0
    eval_tps = (eval_count / (eval_dur_ms / 1000)) if eval_dur_ms > 0 else 0

    return {
        "label": label,
        "total_ms": total_ms,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_dur_ms": prompt_eval_dur_ms,
        "eval_count": eval_count,
        "eval_dur_ms": eval_dur_ms,
        "prompt_tps": prompt_tps,
        "eval_tps": eval_tps,
        "reply": data.get("message", {}).get("content", "")[:60] + "...",
    }


def print_result(r: dict) -> None:
    print(f"\n  【{r['label']}】")
    print(f"  ├─ 總延遲         : {r['total_ms']:>8.0f} ms")
    print(f"  ├─ Prefill 延遲   : {r['prompt_eval_dur_ms']:>8.0f} ms  ({r['prompt_eval_count']} tokens, {r['prompt_tps']:.0f} tok/s)")
    print(f"  ├─ Decode 延遲    : {r['eval_dur_ms']:>8.0f} ms  ({r['eval_count']} tokens, {r['eval_tps']:.1f} tok/s)")
    print(f"  └─ 回覆預覽       : {r['reply']}")


# ─── 主程式 ────────────────────────────────────────────────────────────────────

async def main() -> None:
    # 先確認 Ollama 是否在線
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(f"{OLLAMA_URL}/api/tags") as r:
                if r.status != 200:
                    print(f"[ERROR] Ollama 回傳 HTTP {r.status}，請確認服務是否運作中。")
                    return
                models_data = await r.json()
                available = [m["name"] for m in models_data.get("models", [])]
    except Exception as e:
        print(f"[ERROR] 無法連線到 Ollama ({OLLAMA_URL}): {e}")
        print("  請確認 Ollama 已啟動（執行 `ollama serve`）。")
        return

    if not any(MODEL in m for m in available):
        print(f"[WARN] 模型 '{MODEL}' 不在 Ollama 已載入清單中：{available}")
        print("  嘗試繼續（Ollama 可能會自動拉取），但如果失敗請先執行：")
        print(f"  ollama pull {MODEL}")
        print()

    print("=" * 65)
    print("  Sherry Platform — n_batch & Prompt Caching 效能基準測試")
    print("=" * 65)
    print(f"  模型: {MODEL}")
    print(f"  System Prompt 長度: ~{len(SYSTEM_PROMPT)} chars")
    print()

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:

        # ── 基準線：舊預設值 (n_batch=512, keep_alive=0) ──────────────────────
        print("[Stage 1] 舊預設值基準線 (n_batch=512, keep_alive=0 → 每次卸載)")
        print("          請稍等，模型首次載入可能較久...")
        r_baseline_cold = await do_request(
            session, keep_alive="0", num_batch=512,
            label="舊設定 · 冷啟動 (基準)",
        )
        print_result(r_baseline_cold)

        # 再測一次：由於 keep_alive=0 表示立即卸載，應與第一次差不多
        r_baseline_warm = await do_request(
            session, keep_alive="0", num_batch=512,
            label="舊設定 · 第2次 (keep_alive=0，模型已再次卸載)",
        )
        print_result(r_baseline_warm)

        # ── 新設定：n_batch=2048, keep_alive=60m ──────────────────────────────
        print()
        print("[Stage 2] 新設定 (n_batch=2048, keep_alive=60m → 快取命中)")
        r_new_cold = await do_request(
            session, keep_alive="60m", num_batch=2048,
            label="新設定 · 第1次 (模型初始載入)",
        )
        print_result(r_new_cold)

        r_new_warm = await do_request(
            session, keep_alive="60m", num_batch=2048,
            label="新設定 · 第2次 (KV 快取命中)",
        )
        print_result(r_new_warm)

        r_new_warm2 = await do_request(
            session, keep_alive="60m", num_batch=2048,
            label="新設定 · 第3次 (KV 快取命中)",
        )
        print_result(r_new_warm2)

    # ── 結果比較 ──────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("  比較總表")
    print("=" * 65)
    print(f"  {'指標':<28} {'舊設定(冷)':>10} {'新設定(冷)':>10} {'新設定(暖)':>10}")
    print("  " + "-" * 62)

    def diff_pct(new: float, old: float) -> str:
        if old == 0:
            return " N/A"
        pct = (new - old) / old * 100
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.0f}%"

    rows = [
        ("總延遲 (ms)",          r_baseline_cold["total_ms"],          r_new_cold["total_ms"],    r_new_warm["total_ms"]),
        ("Prefill 延遲 (ms)",    r_baseline_cold["prompt_eval_dur_ms"], r_new_cold["prompt_eval_dur_ms"], r_new_warm["prompt_eval_dur_ms"]),
        ("Prefill 速度 (tok/s)", r_baseline_cold["prompt_tps"],         r_new_cold["prompt_tps"],  r_new_warm["prompt_tps"]),
        ("Decode 速度 (tok/s)",  r_baseline_cold["eval_tps"],           r_new_cold["eval_tps"],    r_new_warm["eval_tps"]),
    ]

    for name, baseline, new_cold, new_warm in rows:
        print(f"  {name:<28} {baseline:>10.1f} {new_cold:>10.1f} {new_warm:>10.1f}  "
              f"(暖 vs 舊冷: {diff_pct(new_warm, baseline)})")

    print()
    prefill_improvement = ((r_baseline_cold["prompt_eval_dur_ms"] - r_new_warm["prompt_eval_dur_ms"])
                           / r_baseline_cold["prompt_eval_dur_ms"] * 100
                           if r_baseline_cold["prompt_eval_dur_ms"] > 0 else 0)
    total_improvement = ((r_baseline_cold["total_ms"] - r_new_warm["total_ms"])
                         / r_baseline_cold["total_ms"] * 100
                         if r_baseline_cold["total_ms"] > 0 else 0)

    print(f"  [結論] Prefill 加速: {prefill_improvement:.1f}%")
    print(f"  [結論] 總延遲加速  : {total_improvement:.1f}%")
    if prefill_improvement > 20:
        print("  >>> 效果顯著！快取與批次處理大幅降低首字延遲。")
    elif prefill_improvement > 5:
        print("  >>> 有效果，特別在長 System Prompt 的情況下更明顯。")
    else:
        print("  >>> 效果有限（可能模型本身已很快或 GPU 不是瓶頸）。")
    print()


if __name__ == "__main__":
    asyncio.run(main())

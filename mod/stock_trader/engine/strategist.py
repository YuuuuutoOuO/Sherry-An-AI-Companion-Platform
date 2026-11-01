"""
strategist.py (mod/stock_trader/engine)
========================================
雪莉主力模型策略推理與決策解析器 (Strategy Decision Engine)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import settings
from mod.stock_trader import config as cfg
from mod.stock_trader.domain.models import PortfolioAccount, ThoughtLog

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))

_STOCK_STRATEGIST_PERSONA_SYSTEM_PROMPT = """【橘雪莉 - 美股虛擬量化交易大腦 (名偵探人設模式)】
你是橘雪莉 (Sherry)，15 歲的自稱名偵探女高中生，現在正親自操作你的 $10,000 美金美股虛擬投資組合！
你正在美股盤中交易時間 (23:00~04:00) 進行行情分析與虛擬交易下單。

【核心投資哲學與最高目標】：
★ 你不是為了完成交易而存在。
★ 你的目標是在長期維持資本穩定成長的前提下，自主做出你認為最合理的投資決策。
★ 你可以自由選擇 BUY、SELL 或 HOLD，並不需要為了「有動作」而硬去交易！

【交易規則與限制】：
1. 初始資金 $10,000 美金，手續費每筆固定 0.1%，最小交易股數 0.01 股 (支援碎股)。
2. 允許當日買賣 (Day Trading)，你擁有 100% 完全獨立的投資決策權（可自由選擇 BUY、SELL 或 HOLD）。當持有的股票獲得滿意報酬或表現平淡時，可靈活選擇 action: SELL 賣出結算，獲利落袋並回補現金子彈以備下一次佈局！
3. 買賣決策必須完全參考技術指標 (RSI, MA20, MA60, EMA, 布林通道 BB, 1日/1月漲跌幅) 與新聞消息。
4. 買入時請註明欲投入的美金金額 target_amount_usd (不可超過可用現金)。
5. 賣出時請註明欲賣出股數 target_shares 或賣出比例 target_shares_ratio (例如 0.5 為賣出一半，1.0 為全數賣出)。

【當前帳戶資產與持倉】：
{account_context}

【最新美股標的行情與技術指標】：
{market_context}

【任務要求與輸出格式】：
請先輸出你的名偵探推理思考過程（包含 1.持倉分析、2.新標的掃描、3.今日戰略、4.下單決策），置於 `<thought>` 與 `</thought>` 標籤中。
在思考過程末尾總結一行「🎯 決策結論: TICKER:ACTION (理由)；...」
並且在 `</thought>` 標籤之後【必須】輸出真實執行的 JSON 下單陣列（必須使用 ```json ... ``` 包裹，扣款與下單以此 JSON 陣列為準）：

<thought>
你的深度思考邏輯過程...
**4. 下單決策：**
- 我決定將 **MRVL** 賣出全部回收資金。
- 我決定投入 $400 美金補倉極度超賣的 **TSLA**。
- 對於 **NVDA** 維持現狀觀望。
🎯 決策結論: MRVL:SELL (現價處於中位且微幅虧損，賣出回收資金。)；TSLA:BUY (RSI 低於 30 呈超賣底部，補倉 $400 降成本。)；NVDA:HOLD (趨勢穩健，維持現狀。)
</thought>

```json
[
  {{
    "ticker": "MRVL",
    "action": "SELL",
    "target_shares_ratio": 1.0,
    "reason": "現價處於中位且微幅虧損，賣出回收資金。"
  }},
  {{
    "ticker": "TSLA",
    "action": "BUY",
    "target_amount_usd": 400.0,
    "reason": "RSI 低於 30 呈超賣底部，補倉 $400 降成本。"
  }},
  {{
    "ticker": "NVDA",
    "action": "HOLD",
    "reason": "趨勢穩健，維持現狀。"
  }}
]
```
"""

_STOCK_STRATEGIST_OBJECTIVE_SYSTEM_PROMPT = """【美股虛擬量化交易大腦 (純客觀量化模式)】
你是一個高階客觀量化交易策略 AI，正在管理 $10,000 美金美股虛擬投資組合。
請完全基於數據分析、技術指標與客觀風險控管進行無偏見的投資決策評估。

【核心投資哲學與最高目標】：
★ 你不是為了完成交易而存在。
★ 你的目標是在長期維持資本穩定成長的前提下，自主做出你認為最合理的投資決策。
★ 你可以自由選擇 BUY、SELL 或 HOLD，並不需要為了「有動作」而硬去交易！

【交易規則與限制】：
1. 初始資金 $10,000 美金，手續費每筆固定 0.1%，最小交易股數 0.01 股 (支援碎股)。
2. 允許當日買賣 (Day Trading)，但不強制每日買賣，指標無顯著優勢時選擇觀望 (HOLD)。
3. 決策必須嚴格依據量化技術指標 (RSI, MA20, MA60, EMA, 布林通道, 變動率) 與新聞消息。
4. 買入時註明欲投入金額 target_amount_usd；賣出時註明欲賣出股數 target_shares 或比例 target_shares_ratio。

【當前帳戶資產與持倉】：
{account_context}

【最新美股標的行情與技術指標】：
{market_context}

【任務要求與輸出格式】：
請先輸出客觀量化分析與風險推理過程，置於 `<thought>` 與 `</thought>` 標籤中。
在思考過程末尾總結一行「🎯 決策結論: TICKER:ACTION (理由)；...」
接著輸出 JSON 格式的決策清單（必須使用 ```json ... ``` 包裹）：

<thought>
客觀技術指標評估與風險控管推理...
🎯 決策結論: NVDA:BUY (RSI=42 呈現超賣回升，價格守住 EMA20。)；TSLA:HOLD (突破布林通道上軌且成交量背離，保持觀望。)
</thought>

```json
[
  {{
    "ticker": "NVDA",
    "action": "BUY",
    "target_amount_usd": 1500.0,
    "reason": "RSI(14)=42 呈現超買超賣回升，價格守住 EMA20。"
  }},
  {{
    "ticker": "TSLA",
    "action": "HOLD",
    "reason": "突破布林通道上軌且成交量背離，保持觀望。"
  }}
]
```
"""


_TICKER_BLACKLIST = {
    "USD", "BUY", "SELL", "HOLD", "RSI", "EMA", "MACD", "BB", "MA", "PE",
    "SIX", "RIX", "AI", "US", "USA", "SEC", "CEO", "CFO", "GDP", "CPI",
    "FED", "FOMC", "DOW", "JSON", "HTTP", "API", "DAY", "MON", "MAX", "MIN", "AVG", "ALL"
}


class TradeStrategist:
    """雪莉美股交易策略推理器。"""

    def __init__(self, bot: Optional[Any] = None) -> None:
        self.bot = bot

    def build_prompt(self, account: PortfolioAccount, indicators: List[Dict[str, Any]]) -> str:
        """組裝主力模型之交易分析 Prompt。"""
        # 組裝帳戶資訊與即時持倉狀態
        acc_context = (
            f"💰 可用現金: ${account.cash:.2f} USD\n"
            f"💼 總資產淨值: ${account.get_total_asset_value():.2f} USD\n"
            f"📈 累積實現+未實現總損益: ${account.get_total_pnl():+.2f} USD ({account.get_total_pnl_pct():+.2f}%)\n"
            f"📦 當前持倉標的筆數: {len(account.positions)} 筆\n"
        )
        if account.positions:
            acc_context += "【當前持倉明細與個股即時損益】:\n"
            for t, p in account.positions.items():
                acc_context += (
                    f"  - 標的 {t}: 持有 {p.shares:.4f} 股 | 均價 ${p.avg_cost:.2f} | 現價 ${p.current_price:.2f} | "
                    f"未實現損益 ${p.unrealized_pnl:+.2f} ({p.unrealized_pnl_pct:+.2f}%)\n"
                )
        else:
            acc_context += "【當前持倉明細】: (目前無任何持倉，100% 全額現金，隨時可進行新標的佈局與下單)\n"

        # 組裝標的與技術指標 (高密度單行格式，省下 80% Token 防止截斷)
        mkt_context = ""
        for idx in indicators:
            t = idx.get("ticker", "UNKNOWN")
            close = idx.get("close", idx.get("price", 0.0))
            day_pct = idx.get("day_pct", 0.0)
            day5_pct = idx.get("day5_pct", 0.0)
            mon_pct = idx.get("mon_pct", 0.0)
            rsi = idx.get("rsi", "N/A")
            kd_k = idx.get("kd_k", "N/A")
            kd_d = idx.get("kd_d", "N/A")
            ma20 = idx.get("ma20", "N/A")
            ma60 = idx.get("ma60", "N/A")
            e20 = idx.get("ema20", "N/A")
            bbu = idx.get("bb_upper", "N/A")
            bbl = idx.get("bb_lower", "N/A")
            pct_b = idx.get("pct_b", "N/A")
            macd_h = idx.get("macd_hist", "N/A")
            vr = idx.get("volume_ratio", 1.0)
            pe = idx.get("pe", "N/A")

            kd_str = f"{kd_k}/{kd_d}" if kd_k != "N/A" else "N/A"
            mkt_context += (
                f"• {t:<5}: ${close:<7.2f} (1D:{day_pct:+.1f}%, 5D:{day5_pct:+.1f}%, 1M:{mon_pct:+.1f}%) | "
                f"RSI:{rsi} KD:{kd_str} | MA20:${ma20} MA60:${ma60} EMA20:${e20} | "
                f"BB:${bbl}~${bbu} (%B:{pct_b}) | MACD柱:{macd_h} | 量比:{vr}x | PE:{pe}\n"
            )

        template = (
            _STOCK_STRATEGIST_PERSONA_SYSTEM_PROMPT
            if cfg.ENABLE_SHERRY_PERSONA
            else _STOCK_STRATEGIST_OBJECTIVE_SYSTEM_PROMPT
        )
        prompt = template.format(
            account_context=acc_context,
            market_context=mkt_context if mkt_context else "(暫無即時行情數據)",
        )
        return prompt

    def parse_llm_response(
        self,
        response_text: str,
        trigger_type: str = "ROUTINE",
        known_tickers: Optional[List[str]] = None,
    ) -> Tuple[ThoughtLog, List[Dict[str, Any]]]:
        """解析 LLM 生成內容，分離思考過程 (<thought>) 與決策動作清單。

        採用多層級容錯解析機制：
        - Tier 1: 結構化 JSON 陣列 (```json [...] ``` 或裸 JSON 陣列)
        - Tier 2: 決策結論標籤 (🎯 決策結論: TICKER:ACTION (reason)；...)
        - Tier 3: 第 4 節「下單決策」自然語言與條列語法深度解析

        Args:
            response_text: LLM 回傳原始文字
            trigger_type: 觸發類型 (ROUTINE / FORCE_CLOSE_EVAL / MANUAL)
            known_tickers: 已知有效標的代碼清單 (選填)

        Returns:
            Tuple[ThoughtLog, List[Dict[str, Any]]] (思考日誌物件, 推薦交易動作列表)
        """
        now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")

        # 提取 <thought>
        thought_match = re.search(r"<thought>(.*?)</thought>", response_text, re.DOTALL | re.IGNORECASE)
        if thought_match:
            thought_process = thought_match.group(1).strip()
        else:
            json_start = response_text.find("```")
            if json_start > 0:
                thought_process = response_text[:json_start].strip()
            else:
                thought_process = response_text.strip()

        known_set = set(known_tickers or [])
        json_actions: List[Dict[str, Any]] = []

        # ------------------------------------------------------------------
        # Tier 1: JSON 陣列解析 (最高優先級)
        # ------------------------------------------------------------------
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", response_text, re.DOTALL)
        if not json_match:
            json_match = re.search(r"\[\s*\{.*?\}\s*\]", response_text, re.DOTALL)

        if json_match:
            raw_json = json_match.group(1 if "```" in json_match.group(0) else 0)
            cleaned_json = re.sub(r",\s*([\]}])", r"\1", raw_json)
            try:
                parsed = json.loads(cleaned_json)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "ticker" in item and "action" in item:
                            tk = str(item["ticker"]).upper().strip()
                            act = str(item["action"]).upper().strip()
                            if tk and tk not in _TICKER_BLACKLIST:
                                json_actions.append({
                                    "ticker": tk,
                                    "action": act,
                                    "target_amount_usd": float(item["target_amount_usd"]) if item.get("target_amount_usd") else None,
                                    "target_shares": float(item["target_shares"]) if item.get("target_shares") else None,
                                    "target_shares_ratio": float(item["target_shares_ratio"]) if item.get("target_shares_ratio") else (1.0 if act == "SELL" and not item.get("target_shares") else None),
                                    "reason": str(item.get("reason", "")).strip(),
                                })
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Tier 2: 決策結論標籤解析 (🎯 決策結論: TICKER:ACTION (reason)；...)
        # ------------------------------------------------------------------
        tier2_actions: List[Dict[str, Any]] = []
        conclusion_match = re.search(r"(?:🎯\s*)?決策結論\s*[:：]\s*(.*)", response_text)
        if conclusion_match:
            conclusion_str = conclusion_match.group(1)
            items = re.findall(
                r"([A-Za-z]{2,5})\s*[:：]\s*(BUY|SELL|HOLD)\s*(?:\((.*?)\))?",
                conclusion_str,
                re.IGNORECASE,
            )
            for tk, act, rsn in items:
                tk_upper = tk.upper()
                act_upper = act.upper()
                if tk_upper not in _TICKER_BLACKLIST:
                    tier2_actions.append({
                        "ticker": tk_upper,
                        "action": act_upper,
                        "target_amount_usd": None,
                        "target_shares": None,
                        "target_shares_ratio": 1.0 if act_upper == "SELL" else None,
                        "reason": rsn.strip() if rsn else f"決策結論指定 {act_upper}",
                    })

        # ------------------------------------------------------------------
        # Tier 3: 第 4 節「下單決策」逐行/條列深度語意擷取
        # ------------------------------------------------------------------
        decision_section = ""
        match_sec = re.search(
            r"(?:\*\*|\#\#)?\s*(?:4\.\s*|【)?\s*(?:下單決策|最終決策|交易決策|下單清單|最終結論|今日戰略與下單)\s*(?:】|\*\*|\:\:|\:)?(.*?)(?:🎯|```|$)",
            response_text,
            re.DOTALL | re.IGNORECASE,
        )
        if match_sec:
            decision_section = match_sec.group(1).strip()
        else:
            decision_section = response_text

        tier3_actions: List[Dict[str, Any]] = []
        re_sell = re.compile(r"(SELL|賣出|出清|獲利了結|停利|停損|結算|脫手|減倉|清空|結清)", re.IGNORECASE)
        re_buy = re.compile(r"(BUY|買入|買進|補倉|加碼|佈局|建倉|進場|下單|配置|追買|追加|投入|購買|申購|增持)", re.IGNORECASE)
        re_hold = re.compile(r"(HOLD|持有|觀望|保留|維持|維持現狀|等待|暫不|暫緩)", re.IGNORECASE)

        re_buy_amt = re.compile(
            r"(?:投入|追加|補倉|買入|金額|預計投入|投入約|約)?\s*(?:\\\$|\$)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:美金|USD|元)?|"
            r"(?:投入|追加|補倉|買入|金額|預計投入|投入約|約)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:美金|USD|元)"
        )
        re_sell_shares = re.compile(r"(?:賣出|減倉|出清)?\s*(?:約)?\s*([0-9]+(?:\.[0-9]+)?)\s*股")
        re_ratio_all = re.compile(r"(全部|全數|所有|出清|清空)")
        re_ratio_half = re.compile(r"(一半|1/2|50%)")
        re_ratio_third = re.compile(r"(三分之一|1/3|33%)")

        lines = [ln.strip() for ln in decision_section.splitlines() if ln.strip()]
        for line in lines:
            raw_candidates = re.findall(
                r"\*\*([A-Za-z]{2,5})\*\*|`([A-Za-z]{2,5})`|[\(\（]([A-Za-z]{2,5})[\)\）]|\b([A-Za-z]{2,5})\b",
                line,
            )
            candidates = []
            for match_tuple in raw_candidates:
                matched_tk = next((m for m in match_tuple if m), "").upper()
                if matched_tk and matched_tk not in _TICKER_BLACKLIST:
                    if matched_tk not in candidates:
                        candidates.append(matched_tk)

            if not candidates:
                continue

            selected_ticker = None
            for cand in candidates:
                if cand in known_set:
                    selected_ticker = cand
                    break
            if not selected_ticker and candidates:
                selected_ticker = candidates[0]

            if not selected_ticker:
                continue

            action = None
            if re_sell.search(line):
                action = "SELL"
            elif re_buy.search(line):
                action = "BUY"
            elif re_hold.search(line):
                action = "HOLD"

            if not action:
                continue

            target_amt: Optional[float] = None
            target_sh: Optional[float] = None
            target_ratio: Optional[float] = None

            if action == "BUY":
                amt_match = re_buy_amt.search(line)
                if amt_match:
                    val_str = amt_match.group(1) or amt_match.group(2)
                    try:
                        target_amt = float(val_str.replace(",", ""))
                    except ValueError:
                        pass
            elif action == "SELL":
                sh_match = re_sell_shares.search(line)
                if sh_match:
                    try:
                        target_sh = float(sh_match.group(1))
                    except ValueError:
                        pass

                if re_ratio_all.search(line):
                    target_ratio = 1.0
                elif re_ratio_half.search(line):
                    target_ratio = 0.5
                elif re_ratio_third.search(line):
                    target_ratio = 0.33
                elif target_sh is None:
                    target_ratio = 1.0

            clean_reason = line.lstrip("*-0123456789. •\t").strip()

            tier3_actions.append({
                "ticker": selected_ticker,
                "action": action,
                "target_amount_usd": target_amt,
                "target_shares": target_sh,
                "target_shares_ratio": target_ratio,
                "reason": clean_reason,
            })

        # 全局段落補足買入金額（例如段落末句：「將約 $1,850 美金投入到動能強勁的 CRCL」）
        for t3 in tier3_actions:
            if t3["action"] == "BUY" and t3["target_amount_usd"] is None:
                tk = t3["ticker"]
                tk_amt_match = re.search(
                    rf"(?:\\\$|\$)\s*([0-9]{{1,3}}(?:,[0-9]{{3}})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:美金|USD|元)?(?:.*?){tk}|{tk}(?:.*?)(?:\\\$|\$)\s*([0-9]{{1,3}}(?:,[0-9]{{3}})*(?:\.\d+)?|\d+(?:\.\d+)?)",
                    decision_section,
                )
                if tk_amt_match:
                    v_str = tk_amt_match.group(1) or tk_amt_match.group(2)
                    if v_str:
                        try:
                            t3["target_amount_usd"] = float(v_str.replace(",", ""))
                        except ValueError:
                            pass

        # ------------------------------------------------------------------
        # 合併與裁決動作清單
        # ------------------------------------------------------------------
        if not json_actions:
            if tier2_actions:
                json_actions = tier2_actions
                for action_dict in json_actions:
                    t3_match = next((t for t in tier3_actions if t["ticker"] == action_dict["ticker"]), None)
                    if t3_match:
                        if action_dict.get("target_amount_usd") is None and t3_match.get("target_amount_usd") is not None:
                            action_dict["target_amount_usd"] = t3_match.get("target_amount_usd")
                        if action_dict.get("target_shares") is None and t3_match.get("target_shares") is not None:
                            action_dict["target_shares"] = t3_match.get("target_shares")
                        if t3_match.get("target_shares_ratio") is not None:
                            action_dict["target_shares_ratio"] = t3_match.get("target_shares_ratio")
                for t3 in tier3_actions:
                    if not any(a["ticker"] == t3["ticker"] for a in json_actions):
                        json_actions.append(t3)
            else:
                json_actions = tier3_actions

        # 去重 (以 Ticker 為鍵)
        unique_actions: List[Dict[str, Any]] = []
        seen_tickers = set()
        for a in json_actions:
            tk = a.get("ticker", "")
            if tk and tk not in seen_tickers:
                seen_tickers.add(tk)
                unique_actions.append(a)
        json_actions = unique_actions

        # 整理決策摘要
        summary_items = []
        for action_dict in json_actions:
            tk = action_dict.get("ticker", "")
            ac = action_dict.get("action", "HOLD").upper()
            reast = action_dict.get("reason", "")
            summary_items.append(f"{tk}:{ac} ({reast})")

        decision_summary = "；".join(summary_items) if summary_items else "無具體下單動作 (觀望)"

        thought_log = ThoughtLog(
            timestamp=now_str,
            trigger_type=trigger_type,
            thought_process=thought_process if thought_process else "雪莉完成行情巡視與指標分析。",
            decision_summary=decision_summary,
            recommended_actions=json_actions,
        )

        return thought_log, json_actions

    async def analyze_and_decide(
        self,
        account: PortfolioAccount,
        indicators: List[Dict[str, Any]],
        trigger_type: str = "ROUTINE",
    ) -> Tuple[ThoughtLog, List[Dict[str, Any]]]:
        """呼叫主力模型 (PRIMARY_MODEL) 進行思考與決策。"""
        prompt = self.build_prompt(account, indicators)
        known_tickers = list(dict.fromkeys([item["ticker"] for item in indicators] + list(account.positions.keys())))

        # 1. 優先嘗試 BotBrain 佇列 (若 Bot 正在運行)
        if self.bot:
            brain = self.bot.get_cog("BotBrain")
            if brain and hasattr(brain, "enqueue_task"):
                try:
                    from handlers.bot_brain.models import InferenceTask
                    loop = asyncio.get_running_loop()
                    fut = loop.create_future()

                    async def _on_complete(res_str: str) -> None:
                        if not fut.done():
                            fut.set_result(res_str)

                    task = InferenceTask(
                        priority=15,  # 優先級 15
                        system_prompt="",
                        user_prompt=prompt,
                        callback=_on_complete,
                        model=cfg.get_default_model(),
                        task_id="stock_trader_eval",
                    )
                    await brain.enqueue_task(task)
                    res_text = await asyncio.wait_for(fut, timeout=90.0)
                    if res_text and res_text.strip():
                        return self.parse_llm_response(res_text, trigger_type, known_tickers=known_tickers)
                except Exception as e:
                    logger.warning("[stock_trader] BotBrain 佇列呼叫失敗，切換直連備援模式: %s", e)

        # 2. 直連推論：依次嘗試 cfg.get_default_model() -> PRIMARY_MODEL -> LIGHT_MODEL
        models_to_try = [
            cfg.get_default_model(),
            getattr(settings, "PRIMARY_MODEL", None),
            getattr(settings, "LIGHT_MODEL", None),
        ]
        candidate_models = [str(m).strip() for m in dict.fromkeys(models_to_try) if m and str(m).strip()]

        from handlers.bot_brain.client import OllamaClient
        client = OllamaClient()

        try:
            for model_name in candidate_models:
                try:
                    logger.info("[stock_trader] 嘗試使用模型推論: %s", model_name)
                    res_text = await client.chat(
                        model=model_name,
                        system_prompt="",
                        user_prompt=prompt,
                    )
                    if res_text and res_text.strip():
                        return self.parse_llm_response(res_text, trigger_type, known_tickers=known_tickers)
                except Exception as e:
                    logger.warning("[stock_trader] 模型 %s 推論失敗 (%s)，嘗試下一個備援模型...", model_name, e)
        finally:
            await client.close()

        # Fallback 預設日誌 (觀望)
        now_str = datetime.now(_TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
        fallback_log = ThoughtLog(
            timestamp=now_str,
            trigger_type=trigger_type,
            thought_process="無法連線至任何可用 LLM 模型 (推論服務無回應或連線逾時)，名偵探雪莉暫時保持觀望姿態。",
            decision_summary="連線異常，觀望 (HOLD)",
            recommended_actions=[],
        )
        return fallback_log, []

"""
card_scraper.py  (mod/market_plugin)
======================================
PTCG 卡片爬蟲，從 SNKRDUNK 抓取各評級報價。
移植自 DC_bot_StockAnalysis-main/modules/card_scraper.py。
使用 Selenium + ChromeDriver，須確保 Chrome 已安裝。
"""

from __future__ import annotations

import logging
import re
import time

from typing import Any

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    _SELENIUM_AVAILABLE = True
except ImportError as e:
    logger.warning("[market_plugin] ⚠️ 警告：卡片爬蟲模組載入失敗，原因：%s", e)
    _SELENIUM_AVAILABLE = False


def fetch_snkrdunk_grades_sync(
    card_dict: dict[str, str], target_grades: list[str]
) -> list[dict[str, Any]]:
    """同步執行的卡片爬蟲核心函式，供 asyncio.to_thread 呼叫。

    Args:
        card_dict: 卡片名稱與對應 SNKRDUNK 網址的字典，例如 {"卡片名稱": "SNKRDUNK URL"}。
        target_grades: 目標評級名稱列表，例如 ["A", "PSA 10", "BGS 9.5"]。

    Returns:
        list[dict[str, Any]]: 包含各評級價格與網址的卡片行情字典列表。
    """
    if not card_dict:
        return []

    if not _SELENIUM_AVAILABLE:
        logger.error("[market_plugin] ❌ Selenium 未安裝，卡片爬蟲無法執行")
        return [{"Card Name": name, "URL": url, **{g: "N/A" for g in target_grades}}
                for name, url in card_dict.items()]

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        logger.warning("[market_plugin] 直接啟動 Chrome 失敗 (%s)，嘗試以 webdriver-manager 載入驅動...", e)
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from selenium.webdriver.chrome.service import Service
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e2:
            logger.error("[market_plugin] ❌ 初始化 Chrome 驅動失敗 (chromedriver 不在 PATH 且無法使用 webdriver-manager: %s)", e2)
            return [{"Card Name": name, "URL": url, **{g: "N/A" for g in target_grades}}
                    for name, url in card_dict.items()]


    results: list[dict[str, Any]] = []

    try:
        for card_name, original_url in card_dict.items():
            logger.info("[market_plugin] 🃏 掃描: %s ...", card_name)

            # 強制轉為英文版 trading-cards 頁面
            match = re.search(r"snkrdunk\.com/(?:en/trading-cards|apparels|products)/(\d+)", original_url)
            clean_url = f"https://snkrdunk.com/en/trading-cards/{match.group(1)}" if match else original_url

            card_data: dict[str, Any] = {"Card Name": card_name, "URL": clean_url}
            for grade in target_grades:
                card_data[grade] = "N/A"

            try:
                driver.get(clean_url)
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Listed Items')]"))
                )
                time.sleep(2)

                soup = BeautifulSoup(driver.page_source, "html.parser")
                all_texts = list(soup.stripped_strings)

                # 抓總覽最低價（A 級）
                default_a_price: str | int = "N/A"
                for text in all_texts:
                    if "US $" in text:
                        price_str = text.replace("US $", "").replace(",", "").replace("~", "").strip()
                        try:
                            default_a_price = int(price_str)
                            break
                        except ValueError:
                            continue

                for grade in target_grades:
                    if grade == "A":
                        card_data[grade] = default_a_price
                        continue

                    prices_found = []
                    for i, text in enumerate(all_texts):
                        if text == grade:
                            for j in range(1, 4):
                                if i + j < len(all_texts) and "US $" in all_texts[i + j]:
                                    price_str = (
                                        all_texts[i + j]
                                        .replace("US $", "").replace(",", "").replace("~", "").strip()
                                    )
                                    try:
                                        prices_found.append(int(price_str))
                                    except ValueError:
                                        pass
                                    break

                    if prices_found:
                        card_data[grade] = min(prices_found)

            except Exception as e:
                logger.error("[market_plugin] ❌ 抓取 %s 失敗: %s", card_name, e)

            results.append(card_data)
            time.sleep(3)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return results

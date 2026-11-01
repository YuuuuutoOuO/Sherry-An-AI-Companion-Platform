"""
core/guard.py
=============
雪莉微核心安全守衛 (CoreGuard)。

功能：
1. 頻率限制 (Rate Limiting) 檢查與過期快取自動回收。
2. 死循環與異常熔斷偵測 (Infinite Loop Circuit Breaker)。
3. SSRF 公網 URL 嚴格安全校驗 (is_safe_public_url)。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
import urllib.parse
from collections import defaultdict

from core.rate_limit import consume_window
from core.state_store import RateLimitStore

logger = logging.getLogger(__name__)


def _is_ip_strictly_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """全面檢查 IP 物件是否為安全之公網位址（嚴格涵蓋 IPv6 映射、6to4、保留網段）。"""
    # 1. 解開 IPv4-mapped IPv6 (::ffff:x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    # 2. 檢查 6to4 映射位址 (2002::/16 內含 IPv4)
    if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.IPv6Network("2002::/16"):
        embedded_ipv4 = ipaddress.IPv4Address(ip.packed[2:6])
        if not _is_ip_strictly_safe(embedded_ipv4):
            return False

    # 3. 雲端 Metadata 與 Carrier-Grade NAT 阻擋 (169.254.0.0/16, 100.64.0.0/10)
    cg_nat = ipaddress.ip_network("100.64.0.0/10")
    cloud_meta = ipaddress.ip_network("169.254.0.0/16")

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
        or (isinstance(ip, ipaddress.IPv4Address) and (ip in cg_nat or ip in cloud_meta))
    ):
        return False

    return True


def is_safe_public_url(url: str) -> bool:
    """檢查目標網址是否為安全的公網 HTTP/HTTPS 位址，防禦 SSRF 攻擊。

    阻擋私有 IP、迴路位址、Link-local、雲端 Metadata 服務 (如 169.254.169.254)、
    Carrier-Grade NAT (RFC 6598) 與非 HTTP(S) 協定。

    Args:
        url: 待檢查的目標網址字串。

    Returns:
        bool: 若為安全公網位址則回傳 True，若存在安全風險或無法解析則回傳 False。
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        clean_host = hostname.strip("[]")

        # 阻擋本機常用別名
        if clean_host.lower() in ("localhost", "local", "broadcasthost", "0.0.0.0"):
            return False

        # 支援十進制整數 IP (如 2130706433)
        if clean_host.isdigit():
            try:
                dec_ip = ipaddress.IPv4Address(int(clean_host))
                return _is_ip_strictly_safe(dec_ip)
            except Exception:
                return False

        # 嘗試直接解析為 IP 物件
        try:
            ip = ipaddress.ip_address(clean_host)
            return _is_ip_strictly_safe(ip)
        except ValueError:
            # clean_host 為網域名稱，繼續進行 DNS 解析
            pass

        # 解析 DNS 記錄並檢查所有解析後的 IP
        addr_info = socket.getaddrinfo(clean_host, None)
        if not addr_info:
            return False

        for res in addr_info:
            sockaddr = res[4]
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if not _is_ip_strictly_safe(ip):
                return False

        return True
    except Exception as exc:
        logger.debug("[guard] URL 安全檢查判定不通過: %s (%s)", url, exc)
        return False


class CoreGuard:
    """微核心安全防護與異常調度守衛。"""

    def __init__(self) -> None:
        try:
            self._user_call_history = RateLimitStore("core_guard_rate")
        except Exception as exc:  # pragma: no cover - 磁碟不可用時降級
            logger.warning("[CoreGuard] SQLite 限流狀態不可用，降級為記憶體：%s", exc)
            self._user_call_history = defaultdict(list)
        self._tool_history: dict[int, list[tuple[str, str]]] = defaultdict(list)

    def check_rate_limit(
        self,
        user_id: int,
        max_calls: int = 5,
        window_sec: float = 10.0,
        scope: str = "default",
    ) -> tuple[bool, int]:
        """檢查特定使用者是否觸發頻率限制。

        Args:
            user_id: Discord User ID。
            max_calls: 時間窗口內允許之最大請求數。
            window_sec: 時間窗口秒數。
            scope: 限流命名空間 (如 default / context_menu)，供不同來源型別獨立計次。

        Returns:
            tuple[bool, int]: (是否允許通過, 需冷卻之秒數)。
        """
        now = time.time()
        bucket_key: tuple[int, str] = (user_id, scope)
        return consume_window(
            self._user_call_history,
            bucket_key,
            max_calls=max_calls,
            window_sec=window_sec,
            now=now,
        )

    def check_tool_loop(self, user_id: int, tool_name: str, tool_input: str, max_consecutive: int = 3) -> bool:
        """偵測 Agent 是否陷入調用相同工具與相同參數之死循環。

        Args:
            user_id: 使用者 ID。
            tool_name: 當前調用工具名。
            tool_input: 工具輸入字串。
            max_consecutive: 觸發熔斷之連續重複次數上限。

        Returns:
            bool: True 表示偵測到死循環（需熔斷），False 表示正常。
        """
        history = self._tool_history[user_id]
        history.append((tool_name.upper(), tool_input.strip()))
        if len(history) > 10:
            self._tool_history[user_id] = history[-10:]

        recent = self._tool_history[user_id][-max_consecutive:]
        if len(recent) == max_consecutive:
            # 1. 檢查最近 N 次是否完全一模一樣
            if all(item == recent[0] for item in recent):
                logger.warning(
                    "CoreGuard: 偵測到死循環熔斷! User %d 工具 [%s] 連續調用 %d 次相同參數",
                    user_id,
                    tool_name,
                    max_consecutive,
                )
                return True

        # 2. 檢查 A -> B -> A -> B 交替震盪死循環
        if len(self._tool_history[user_id]) >= 4:
            h = self._tool_history[user_id]
            if h[-4] == h[-2] and h[-3] == h[-1] and h[-4] != h[-3]:
                logger.warning(
                    "CoreGuard: 偵測到震盪死循環熔斷 (A->B->A->B)! User %d: [%s] 與 [%s]",
                    user_id,
                    h[-2][0],
                    h[-1][0],
                )
                return True

        return False

    def clear_user_history(self, user_id: int) -> None:
        """清除使用者的工具歷史與全數限流計次快取。"""
        self._tool_history.pop(user_id, None)
        for key in [k for k in list(self._user_call_history.keys()) if k[0] == user_id]:
            self._user_call_history.pop(key, None)

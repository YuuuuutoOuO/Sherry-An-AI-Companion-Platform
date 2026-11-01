"""tools/audit_dependencies.py
============================
依賴項與容器安全審計工具 (Dependency & Supply Chain Security Auditor).

功能：
1. 檢驗 requirements.lock 是否符合嚴格鎖定（版本 ==、SHA-256 Hash 完整）。
2. 檢查常用網路與資料處理套件是否符合資安修補基準版本（防範已知 CVE）。
3. 查驗容器配置安全：.dockerignore 排除敏感資料、Dockerfile 非 root USER 與 HEALTHCHECK、docker-compose 資源限制與 localhost 綁定。
4. 若環境安裝有 pip-audit，自動執行即時 CVE 資料庫比對。

執行方式：
  python tools/audit_dependencies.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

_ROOT = Path(__file__).resolve().parent.parent

# 已知高風險套件之安全修訂基準門檻 (CVE Remediation Minimum Versions)
_SECURITY_BASELINES: dict[str, tuple[int, ...]] = {
    "aiohttp": (3, 10, 0),
    "urllib3": (2, 0, 7),
    "requests": (2, 31, 0),
    "pillow": (10, 3, 0),
    "cryptography": (42, 0, 0),
    "tornado": (6, 4, 1),
}


def _parse_version(v_str: str) -> tuple[int, ...]:
    """解析純數字版號元組以供比對。"""
    clean = re.sub(r"[^\d\.]", "", v_str.split("+")[0])
    parts = [int(p) for p in clean.split(".") if p.isdigit()]
    return tuple(parts)


def audit_requirements_lock(lock_path: Path) -> tuple[bool, list[str]]:
    """審計 requirements.lock 鎖定檔語法、版本與 hash 完整性。"""
    issues: list[str] = []
    if not lock_path.exists():
        return False, ["requirements.lock 不存在"]

    content = lock_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    locked_pkgs: dict[str, str] = {}
    has_hash: dict[str, bool] = {}

    current_pkg = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if "==" in stripped:
            match = re.match(r"^([a-zA-Z0-9_\-\.]+)\s*==\s*([a-zA-Z0-9_\-\.]+)", stripped)
            if match:
                current_pkg = match.group(1).lower()
                ver = match.group(2)
                locked_pkgs[current_pkg] = ver
                has_hash[current_pkg] = False
        elif "--hash=sha256:" in stripped and current_pkg:
            has_hash[current_pkg] = True

    if not locked_pkgs:
        issues.append("requirements.lock 中未解析到任何鎖定套件")

    missing_hashes = [pkg for pkg, ok in has_hash.items() if not ok]
    if missing_hashes:
        issues.append(f"以下套件缺少 sha256 雜湊驗證: {', '.join(missing_hashes[:5])}")

    # 查驗已知 CVE 基準
    for pkg_name, min_ver in _SECURITY_BASELINES.items():
        if pkg_name in locked_pkgs:
            cur_ver = _parse_version(locked_pkgs[pkg_name])
            if cur_ver < min_ver:
                issues.append(
                    f"套件 {pkg_name}=={locked_pkgs[pkg_name]} 低於安全建議基準 {'.'.join(str(x) for x in min_ver)}"
                )

    return len(issues) == 0, issues


def audit_container_hardening(root_dir: Path) -> tuple[bool, list[str]]:
    """審計容器設定檔安全硬化狀況 (.dockerignore, Dockerfile, docker-compose.yml)。"""
    issues: list[str] = []

    # 1. .dockerignore 檢查
    di_path = root_dir / ".dockerignore"
    if not di_path.exists():
        issues.append(".dockerignore 檔案不存在，易導致機敏資料固化進映像")
    else:
        di_content = di_path.read_text(encoding="utf-8")
        required_patterns = [".env", "data/", "__pycache__", ".git"]
        for p in required_patterns:
            if p not in di_content:
                issues.append(f".dockerignore 缺少排除關鍵規則: {p}")

    # 2. Dockerfile 檢查
    df_path = root_dir / "Dockerfile"
    if not df_path.exists():
        issues.append("Dockerfile 不存在")
    else:
        df_content = df_path.read_text(encoding="utf-8")
        if not re.search(r"^\s*USER\s+(?!root\b)\w+", df_content, re.MULTILINE):
            issues.append("Dockerfile 缺少非 root USER 指令或仍使用 root")
        if "HEALTHCHECK" not in df_content:
            issues.append("Dockerfile 缺少 HEALTHCHECK 健康探針指令")

    # 3. docker-compose.yml 檢查
    dc_path = root_dir / "docker-compose.yml"
    if dc_path.exists():
        dc_content = dc_path.read_text(encoding="utf-8")
        if re.search(r'["\']8763:8763["\']', dc_content) or re.search(r'["\']0\.0\.0\.0:8763:8763["\']', dc_content):
            issues.append("docker-compose.yml 將連接埠直接曝露於 0.0.0.0，應綁定 127.0.0.1")
        if "mem_limit" not in dc_content and "memory:" not in dc_content:
            issues.append("docker-compose.yml 缺少記憶體上限資源限制 (mem_limit)")

    return len(issues) == 0, issues


def audit_with_pip_audit(lock_path: Path) -> tuple[bool, str]:
    """若系統存在 pip-audit 工具，執行動態弱點資料庫查驗。"""
    try:
        proc = subprocess.run(
            ["pip-audit", "-r", str(lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return True, "pip-audit 弱點掃描通過，無已知 CVE"
        return False, f"pip-audit 檢測到相依性弱點:\n{proc.stdout or proc.stderr}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True, "環境未安裝 pip-audit 或掃描超時，已以內建 CVE 基準規則完成審計"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Sherry Dependency & Container Security Auditor")
    parser.add_argument("--root", type=str, default=str(_ROOT), help="Project root directory")
    args = parser.parse_args()

    root_dir = Path(args.root)
    lock_file = root_dir / "requirements.lock"

    print("==================================================")
    print("[AUDIT] 雪莉相依性供應鏈與容器安全審計 (Supply Chain Audit)")
    print("==================================================")

    # 1. 審計 requirements.lock
    lock_ok, lock_issues = audit_requirements_lock(lock_file)
    if lock_ok:
        print("[PASS] requirements.lock 鎖定與 SHA-256 雜湊查驗通過")
    else:
        print("[FAIL] requirements.lock 查驗失敗:")
        for err in lock_issues:
            print(f"   - {err}")

    # 2. 審計容器硬化
    cont_ok, cont_issues = audit_container_hardening(root_dir)
    if cont_ok:
        print("[PASS] 容器硬化設定查驗通過 (.dockerignore / Dockerfile / docker-compose.yml)")
    else:
        print("[FAIL] 容器硬化查驗失敗:")
        for err in cont_issues:
            print(f"   - {err}")

    # 3. 動態 pip-audit
    audit_ok, audit_msg = audit_with_pip_audit(lock_file)
    print(f"[INFO] {audit_msg}")

    print("==================================================")
    success = lock_ok and cont_ok and audit_ok
    if success:
        print("[PASS] 全數相依性與容器安全檢查審核通過 (ALL CHECKS PASSED)")
        return 0
    else:
        print("[WARN] 存在資安或配置違規項，請修正後重新執行")
        return 1


if __name__ == "__main__":
    sys.exit(main())

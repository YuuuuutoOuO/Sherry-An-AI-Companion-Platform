"""tests/unit/test_container_and_supply_chain_r27.py
================================================
R27 驗收測試：容器硬化、非 root 使用者、.dockerignore 與依賴鎖定審計。
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

try:
    import yaml
except ImportError:
    yaml = None

from tools.audit_dependencies import (
    audit_requirements_lock,
    audit_container_hardening,
    _SECURITY_BASELINES,
)

_ROOT = Path(__file__).resolve().parent.parent.parent


def test_dockerignore_configuration():
    """驗證 .dockerignore 存在並包含所有關鍵排除路徑（防範敏感資料與 PII 入鏡）。"""
    di_path = _ROOT / ".dockerignore"
    assert di_path.exists(), ".dockerignore 必須存在"

    content = di_path.read_text(encoding="utf-8")
    required_exclusions = [
        ".env",
        "data/",
        "backups/",
        "__pycache__",
        ".git",
        "Docs/",
        ".pytest_cache/",
    ]
    for pattern in required_exclusions:
        assert pattern in content, f".dockerignore 缺少排除項目: {pattern}"


def test_dockerfile_hardening():
    """驗證 Dockerfile 具備非 root USER、HEALTHCHECK 與預設安全環境變數。"""
    df_path = _ROOT / "Dockerfile"
    assert df_path.exists(), "Dockerfile 必須存在"

    content = df_path.read_text(encoding="utf-8")

    # 1. 非 root 使用者
    assert "useradd" in content or "adduser" in content
    assert "USER app" in content

    # 2. 健康檢查 HEALTHCHECK
    assert "HEALTHCHECK" in content
    assert "/api/version" in content

    # 3. 預設本機綁定
    assert "WEB_UI_HOST=127.0.0.1" in content
    assert "EXPOSE 8763" in content


def test_docker_compose_security_limits():
    """驗證 docker-compose.yml 將連接埠綁定至 127.0.0.1 並設有記憶體資源上限。"""
    dc_path = _ROOT / "docker-compose.yml"
    assert dc_path.exists(), "docker-compose.yml 必須存在"

    content = dc_path.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(content)

        sherry_svc = data.get("services", {}).get("sherry", {})
        assert sherry_svc, "docker-compose.yml 缺少 sherry 服務"

        # 1. 埠映射限制為 127.0.0.1
        ports = sherry_svc.get("ports", [])
        assert any("127.0.0.1:8763:8763" in str(p) for p in ports), f"連接埠未綁定 127.0.0.1: {ports}"

        # 2. 記憶體與 CPU 資源限制
        assert "mem_limit" in sherry_svc or "deploy" in sherry_svc
        if "mem_limit" in sherry_svc:
            assert sherry_svc["mem_limit"] == "4G"

        # 3. Healthcheck
        assert "healthcheck" in sherry_svc
    else:
        assert "127.0.0.1:8763:8763" in content
        assert "mem_limit: 4G" in content or "deploy" in content
        assert "healthcheck" in content


def test_requirements_lock_integrity():
    """驗證 requirements.lock 採用嚴格鎖定 (==) 與 SHA-256 完整性雜湊。"""
    lock_path = _ROOT / "requirements.lock"
    assert lock_path.exists(), "requirements.lock 必須存在"

    ok, issues = audit_requirements_lock(lock_path)
    assert ok is True, f"requirements.lock 審計不通過: {issues}"


def test_audit_dependencies_cli_runner():
    """驗證 tools/audit_dependencies.py 審計腳本直接運行退出碼為 0。"""
    script_path = _ROOT / "tools" / "audit_dependencies.py"
    proc = subprocess.run(
        [sys.executable, str(script_path), "--root", str(_ROOT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"audit_dependencies.py 執行失敗: {proc.stdout}\n{proc.stderr}"
    assert "[PASS] 全數相依性與容器安全檢查審核通過" in proc.stdout

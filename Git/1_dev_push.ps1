﻿﻿﻿[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 切換至專案根目錄
Set-Location "$PSScriptRoot\.."

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "          Git 自動 Push - 單純記錄開發進度 (dev)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# 取得當前日期 (格式: YY.MM.DD)
$dateStr = (Get-Date).ToString("yy.MM.dd")

# 查詢 Git commit log 與 tags 中今日已存在的序號
$gitLog = git log --all --oneline --format="%s" 2>$null
$gitTags = git tag 2>$null

$allHistory = @()
if ($gitLog) { $allHistory += $gitLog }
if ($gitTags) { $allHistory += $gitTags }

$maxSeq = 0
$pattern = "^" + [regex]::Escape($dateStr) + "\.(\d{3})"

foreach ($item in $allHistory) {
    if ($item -match $pattern) {
        $num = [int]$matches[1]
        if ($num -gt $maxSeq) {
            $maxSeq = $num
        }
    }
}

$nextSeq = ($maxSeq + 1).ToString("000")
$baseName = "$dateStr.$nextSeq"

Write-Host "今日日期編號: $baseName" -ForegroundColor Yellow
Write-Host ""
$ccc = Read-Host "請輸入記錄開發內容 (若無請直接按 ENTER)"
$ccc = $ccc.Trim()

if ($ccc -ne "") {
    $commitName = "$baseName-$ccc"
} else {
    $commitName = $baseName
}

Write-Host ""
Write-Host ">>> 準備提交 Commit: $commitName" -ForegroundColor Green
Write-Host ""

# 切換至 dev 分支
Write-Host "[1/4] 切換至 dev 分支..." -ForegroundColor Yellow
git checkout dev
if ($LASTEXITCODE -ne 0) {
    git checkout -b dev 2>$null
}

Write-Host "[2/4] 暫存變更 (git add .)..." -ForegroundColor Yellow
git add .

Write-Host "[3/4] 提交變更 (git commit)..." -ForegroundColor Yellow
$status = git status --porcelain
if ($status) {
    git commit -m "$commitName"
} else {
    Write-Host "提示: 沒有未提交的檔案變更，準備推送已有 commit..." -ForegroundColor DarkYellow
}

Write-Host "[4/4] 推送至遠端 (git push origin dev)..." -ForegroundColor Yellow
git push origin dev

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Green
    Write-Host " SUCCESS: 成功完成 Push 至 dev 分支!" -ForegroundColor Green
    Write-Host " Commit 名稱: $commitName" -ForegroundColor Green
    Write-Host "===================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Red
    Write-Host " ERROR: Push 過程發生錯誤，請檢查 Git 狀態或遠端庫。" -ForegroundColor Red
    Write-Host "===================================================" -ForegroundColor Red
}

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location "$PSScriptRoot\.."

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "         Git 自動 Push - 大版本推送 (main)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# 計算大版本號: 撈 Tag + Commit 訊息中最大的 v1.X，往上加 1
$tags = git tag -l "v1.*" 2>$null
$gitLog = git log --all --format="%s" 2>$null
$allRef = @()
if ($tags) { $allRef += $tags }
if ($gitLog) { $allRef += $gitLog }

$maxX = 0
$oldMaxTag = ""
$oldMaxX = 0
$oldMaxZ = 0

foreach ($item in $allRef) {
    if ($item -match "v1\.(\d+)\.(\d+)") {
        $x = [int]$matches[1]; $z = [int]$matches[2]
        if ($x -gt $maxX) { $maxX = $x }
        
        # 找出目前實際上最大的完整 Tag 名稱 (用來稍後刪除)
        if ($x -gt $oldMaxX -or ($x -eq $oldMaxX -and $z -gt $oldMaxZ)) {
            $oldMaxX = $x
            $oldMaxZ = $z
            $oldMaxTag = "v1.${x}.${z}"
        }
    }
    # 支援只有 v1.x 的格式相容
    elseif ($item -match "v1\.(\d+)\.") {
        $x = [int]$matches[1]
        if ($x -gt $maxX) { $maxX = $x }
    }
}
$nextX = $maxX + 1
$versionTag = "v1.${nextX}.0"
$baseName = "Release $versionTag"

Write-Host "版本計算: $versionTag  (舊最大版本: $oldMaxTag)" -ForegroundColor Yellow
Write-Host ""
$ccc = Read-Host "請輸入大版本推送描述 CCC (若無請直接按 ENTER)"
$ccc = $ccc.Trim()
$commitName = if ($ccc -ne "") { "$baseName-$ccc" } else { $baseName }

Write-Host ""
Write-Host ">>> Commit / Tag 名稱: $commitName" -ForegroundColor Green
Write-Host ""

# 記錄目前分支
$originalBranch = git branch --show-current
if ($originalBranch -eq "main") {
    Write-Host "警告: 你目前已經在 main 分支，此腳本建議在 dev 分支執行以打包最新進度。" -ForegroundColor Red
    $originalBranch = "dev"
}

# 1. 確保目前分支的工作都有被存下來
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "[1/5] 自動將未提交的變更存入 $originalBranch..." -ForegroundColor Yellow
    git add .
    git commit -m "Auto commit before major release push"
} else {
    Write-Host "[1/5] 工作區無變更，繼續執行。" -ForegroundColor DarkYellow
}

# 2. 切換至 main 分支
Write-Host "[2/5] 切換至 main 分支..." -ForegroundColor Yellow
git checkout main
if ($LASTEXITCODE -ne 0) { git checkout -b main 2>$null }

$currentBranch = git branch --show-current
if ($currentBranch -ne "main") {
    Write-Host " ERROR: 無法切換至 main！目前在 [$currentBranch]" -ForegroundColor Red
    exit 1
}

# 3. 取得 dev 最新程式碼 (強制作業區同步)
Write-Host "[3/5] 將 $originalBranch 程式碼同步至 main..." -ForegroundColor Yellow
git read-tree -u --reset $originalBranch
git add .

# 4. 提交
Write-Host "[4/5] 提交全新 Commit..." -ForegroundColor Yellow
$dirty2 = git status --porcelain
if ($dirty2) {
    git commit -m "$commitName"
} else {
    git commit --allow-empty -m "$commitName"
}

# 5. 建立 Tag 並推送 (包含刪除舊的 Tag 機制)
Write-Host "[5/5] 建立 Tag ($versionTag) 並推送..." -ForegroundColor Yellow

# 刪除前一個舊版本的 Tag
if ($oldMaxTag -ne "") {
    Write-Host "  -> 刪除舊版本 Tag: $oldMaxTag" -ForegroundColor DarkYellow
    git push origin :refs/tags/$oldMaxTag 2>$null
    git tag -d $oldMaxTag 2>$null
}

# 確保不會跟自己新建的同名衝到 (防呆)
git push origin :refs/tags/$versionTag 2>$null
git tag -d $versionTag 2>$null

git tag -a "$versionTag" -m "$commitName"
git push origin main
git push origin "$versionTag" --force

# 切回原本分支
if ($originalBranch -and $originalBranch -ne "main") {
    Write-Host ""
    Write-Host "切回原本分支 ($originalBranch)..." -ForegroundColor Yellow
    git checkout $originalBranch 2>$null
}

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Green
    Write-Host " SUCCESS  |  大版本 Push 至 main 完成!" -ForegroundColor Green
    Write-Host " Commit   |  $commitName" -ForegroundColor Green
    Write-Host " Tag      |  $versionTag" -ForegroundColor Green
    Write-Host "===================================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Red
    Write-Host " ERROR  |  Push 失敗，請檢查 Git 狀態。" -ForegroundColor Red
    Write-Host "===================================================" -ForegroundColor Red
}

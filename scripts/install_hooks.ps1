$hookSrc = Join-Path $PSScriptRoot "git-hooks\commit-msg"
$hookDest = Join-Path $PSScriptRoot "..\.git\hooks\commit-msg"

if (Test-Path $hookSrc) {
    $bytes = [System.IO.File]::ReadAllBytes($hookSrc)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $lfText = $text -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($hookDest, $lfText, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "Successfully installed commit-msg hook with LF line endings."
} else {
    Write-Error "Source hook not found at $hookSrc"
}

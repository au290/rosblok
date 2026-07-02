# vps_update.ps1 - keep server.py current from raw GitHub on a WINDOWS VPS (no git).
# Mirrors the phone's autoupdate.sh. Run from the folder holding server.py/token.txt/config.txt:
#
#   powershell -ExecutionPolicy Bypass -File vps_update.ps1
#
# Every 60s it re-downloads server.py; if it changed, overwrites + restarts the bot.
# Bot stdout/stderr go to bot.log / bot.err.log.  Ctrl-C to stop the updater.

$ProgressPreference = "SilentlyContinue"   # hide Invoke-WebRequest's progress bar (also much faster)
Set-Location -Path $PSScriptRoot
$Raw   = "https://raw.githubusercontent.com/au290/rosblok/main"
$Entry = "server.py"
$Py    = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { $Py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $Py) { Write-Error "python not found in PATH"; exit 1 }

$script:proc = $null

function Restart-Bot {
    if ($script:proc -and -not $script:proc.HasExited) {
        Stop-Process -Id $script:proc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    $script:proc = Start-Process -FilePath $Py -ArgumentList $Entry `
        -RedirectStandardOutput "bot.log" -RedirectStandardError "bot.err.log" `
        -NoNewWindow -PassThru
    Write-Host "[vps_update] $(Get-Date -Format HH:mm:ss) $Entry (re)started"
}

function Hash-Of($path) {
    if (Test-Path $path) { (Get-FileHash $path -Algorithm MD5).Hash } else { "" }
}

Restart-Bot
while ($true) {
    try {
        Invoke-WebRequest -Uri "$Raw/$Entry" -OutFile ".$Entry.new" -UseBasicParsing -TimeoutSec 20
        if ((Test-Path ".$Entry.new") -and ((Get-Item ".$Entry.new").Length -gt 0)) {
            if ((Hash-Of ".$Entry.new") -ne (Hash-Of $Entry)) {
                Move-Item ".$Entry.new" $Entry -Force
                Write-Host "[vps_update] $(Get-Date -Format HH:mm:ss) $Entry changed - updating"
                Restart-Bot
            } else {
                Remove-Item ".$Entry.new" -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Host "[vps_update] fetch error: $_"
    }
    Start-Sleep -Seconds 60
}

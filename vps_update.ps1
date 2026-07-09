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
    # kill EVERY python running our entry file (not just the one we started), so
    # restarts never accumulate duplicate bots that thrash Discord's gateway
    Get-CimInstance Win32_Process -Filter "name='python.exe'" |
        Where-Object { $_.CommandLine -match [regex]::Escape($Entry) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
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
    $wait = 60
    try {
        Invoke-WebRequest -Uri "$Raw/$Entry" -OutFile ".$Entry.new" -UseBasicParsing -TimeoutSec 20
        if ((Test-Path ".$Entry.new") -and ((Get-Item ".$Entry.new").Length -gt 0)) {
            # only accept a download that compiles as Python — a GitHub 429/abuse page is
            # non-empty and would otherwise clobber server.py and restart into a dead bot.
            & $Py -m py_compile ".$Entry.new" 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[vps_update] $(Get-Date -Format HH:mm:ss) bad download (not Python - likely GitHub 429); keeping current"
                Remove-Item ".$Entry.new" -ErrorAction SilentlyContinue
                $wait = 300                        # back off so we stop tripping the rate limit
            } elseif ((Hash-Of ".$Entry.new") -ne (Hash-Of $Entry)) {
                Move-Item ".$Entry.new" $Entry -Force
                Write-Host "[vps_update] $(Get-Date -Format HH:mm:ss) $Entry changed - updating"
                Restart-Bot
            } else {
                Remove-Item ".$Entry.new" -ErrorAction SilentlyContinue
            }
        }
    } catch {
        # Invoke-WebRequest throws on 429/4xx; back off instead of hammering
        if ("$_" -match "429|rate") { $wait = 300 }
        Write-Host "[vps_update] fetch error: $_"
    }
    Start-Sleep -Seconds $wait
}

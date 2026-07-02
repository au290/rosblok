# monitor.ps1 - tiny 24h CPU/RAM monitor for a small Windows Server (1 vCPU / 2 GB).
# Samples every $IntervalSec, keeps a rolling 24h stats.csv, and regenerates stats.html
# with a Chart.js graph you open over RDP. No service, no install, ~0 overhead:
# uses cheap CIM counters (not Get-Counter) and a 60s interval.
#
#   powershell -ExecutionPolicy Bypass -File monitor.ps1
# Then open stats.html in the browser (it auto-refreshes every 60s). Ctrl-C to stop.
# Lighter still: -IntervalSec 120.  Longer history: -RetentionHours 48.

param(
    [int]$IntervalSec    = 60,       # 60s = 1440 points / 24h
    [int]$RetentionHours = 24,
    [string]$OutDir      = $PSScriptRoot,
    [string]$WebhookUrl  = ""         # Discord webhook; edits ONE message instead of reposting
)

$Csv       = Join-Path $OutDir "stats.csv"
$Html      = Join-Path $OutDir "stats.html"
$MsgIdFile = Join-Path $OutDir "monitor_msg.txt"   # remembers the webhook message id to PATCH

# webhook.txt (gitignored) overrides the param, so the URL isn't hardcoded in the script
$WebhookFile = Join-Path $OutDir "webhook.txt"
if (-not $WebhookUrl -and (Test-Path $WebhookFile)) { $WebhookUrl = (Get-Content $WebhookFile -Raw).Trim() }

function Get-Sample {
    $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    if ($null -eq $cpu) { $cpu = 0 }
    $os     = Get-CimInstance Win32_OperatingSystem
    $totMB  = [math]::Round($os.TotalVisibleMemorySize / 1024)
    $usedMB = $totMB - [math]::Round($os.FreePhysicalMemory / 1024)
    $memPct = if ($totMB) { [math]::Round(($usedMB / $totMB) * 100, 1) } else { 0 }
    [pscustomobject]@{
        time        = (Get-Date).ToString("s")
        cpu         = [math]::Round($cpu)
        mem_pct     = $memPct
        mem_used_mb = $usedMB
    }
}

function Write-Html($rows) {
    $labels = ($rows | ForEach-Object { '"' + ($_.time -replace 'T',' ') + '"' }) -join ','
    $cpu    = ($rows | ForEach-Object { $_.cpu })     -join ','
    $mem    = ($rows | ForEach-Object { $_.mem_pct }) -join ','
    $peakC  = ($rows | Measure-Object cpu     -Maximum).Maximum
    $peakM  = ($rows | Measure-Object mem_pct -Maximum).Maximum
    $html = @"
<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="$IntervalSec">
<title>VPS monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>body{font-family:Segoe UI,sans-serif;background:#111;color:#eee;margin:24px}
h2{font-weight:500}small{color:#888}</style></head><body>
<h2>CPU / Memory - last $RetentionHours h &nbsp;<small>peak CPU $peakC% - peak mem $peakM% - $($rows.Count) samples</small></h2>
<canvas id="c" height="110"></canvas>
<script>
new Chart(document.getElementById('c'),{type:'line',
 data:{labels:[$labels],datasets:[
  {label:'CPU %',data:[$cpu],borderColor:'#e74c3c',backgroundColor:'#e74c3c22',fill:true,pointRadius:0,tension:.2},
  {label:'Mem %',data:[$mem],borderColor:'#3498db',backgroundColor:'#3498db22',fill:true,pointRadius:0,tension:.2}]},
 options:{animation:false,responsive:true,interaction:{mode:'index',intersect:false},
  scales:{y:{min:0,max:100,ticks:{color:'#aaa'},grid:{color:'#222'}},
          x:{ticks:{color:'#aaa',maxTicksLimit:12},grid:{color:'#1a1a1a'}}},
  plugins:{legend:{labels:{color:'#eee'}}}}});
</script></body></html>
"@
    $html | Out-File -Encoding utf8 $Html
}

function Spark($vals) {
    $b = 0x2581..0x2588 | ForEach-Object { [char]$_ }      # levels: lowest .. full block
    $last = @($vals | Select-Object -Last 30)
    if ($last.Count -eq 0) { return "" }
    -join ($last | ForEach-Object {
        $i = [int][math]::Round(([double]$_ / 100) * 7)
        if ($i -lt 0) { $i = 0 } elseif ($i -gt 7) { $i = 7 }
        $b[$i]
    })
}

function Build-Embed($rows) {
    $now    = $rows[-1]
    $cpuNow = [int]$now.cpu
    $memNow = [double]$now.mem_pct
    $peakC  = ($rows | Measure-Object cpu     -Maximum).Maximum
    $peakM  = ($rows | Measure-Object mem_pct -Maximum).Maximum
    $worst  = [math]::Max($cpuNow, $memNow)
    $color  = if ($worst -ge 90) { 15158332 } elseif ($worst -ge 70) { 15105570 } else { 3066993 }  # red/orange/green
    $bt3    = ([char]96).ToString() * 3
    $nl     = [char]10
    $desc   = $bt3 + $nl +
              ("CPU {0} {1,3}%" -f (Spark ($rows | ForEach-Object { $_.cpu })),     $cpuNow)      + $nl +
              ("MEM {0} {1,3}%" -f (Spark ($rows | ForEach-Object { $_.mem_pct })), [int]$memNow) + $nl +
              $bt3
    return @{
        title       = "VPS monitor - last ${RetentionHours}h"
        color       = $color
        description  = $desc
        fields      = @(
            @{ name = "CPU now";  value = "$cpuNow%";                          inline = $true }
            @{ name = "Mem now";  value = "$memNow% ($($now.mem_used_mb) MB)"; inline = $true }
            @{ name = "24h peak"; value = "CPU $peakC% / Mem $peakM%";         inline = $true }
        )
        footer      = @{ text = "updated $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $($rows.Count) samples" }
    }
}

function Update-Discord($rows) {
    if (-not $WebhookUrl) { return }
    $body  = @{ embeds = @((Build-Embed $rows)) } | ConvertTo-Json -Depth 8 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)          # force UTF-8 (sparkline chars)
    $id = if (Test-Path $MsgIdFile) { (Get-Content $MsgIdFile -Raw).Trim() } else { "" }
    if ($id) {
        try {
            Invoke-RestMethod -Uri "$WebhookUrl/messages/$id" -Method Patch `
                -ContentType 'application/json' -Body $bytes | Out-Null
            return                                                # edited the existing message
        } catch {
            Remove-Item $MsgIdFile -ErrorAction SilentlyContinue  # message gone - recreate below
        }
    }
    try {
        $r = Invoke-RestMethod -Uri "$($WebhookUrl)?wait=true" -Method Post `
            -ContentType 'application/json' -Body $bytes
        if ($r.id) { $r.id | Out-File -Encoding ascii $MsgIdFile }
    } catch {
        Write-Host "[monitor] webhook error: $_"
    }
}

$sink = if ($WebhookUrl) { "Discord + $Html" } else { $Html }
Write-Host "[monitor] sampling every ${IntervalSec}s, ${RetentionHours}h window -> $sink"
while ($true) {
    try {
        $rows = @()
        if (Test-Path $Csv) { $rows = @(Import-Csv $Csv) }
        $rows += Get-Sample
        $cut  = (Get-Date).AddHours(-$RetentionHours)
        $rows = @($rows | Where-Object { $_.time -and ([datetime]$_.time -ge $cut) })
        $rows | Export-Csv $Csv -NoTypeInformation -Encoding ascii
        Write-Html $rows
        Update-Discord $rows
    } catch {
        Write-Host "[monitor] $(Get-Date -Format HH:mm:ss) error: $_"
    }
    Start-Sleep -Seconds $IntervalSec
}

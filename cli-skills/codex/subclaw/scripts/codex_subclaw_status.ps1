param(
  [string]$ProxyUrl = "http://127.0.0.1:4748",
  [string]$ReportsDir = "",
  [int]$Recent = 10,
  [switch]$Watch,
  [int]$IntervalSec = 5,
  [int]$MaxSeconds = 0
)

$ErrorActionPreference = "Stop"

function Get-Json($Url) {
  try {
    return Invoke-RestMethod -Uri $Url -TimeoutSec 5
  } catch {
    Write-Output "proxy_error: $($_.Exception.Message)"
    return $null
  }
}

function Render-Snapshot {
  $status = Get-Json "$ProxyUrl/api/status"
  $models = Get-Json "$ProxyUrl/models"
  if (-not $status -or -not $models) {
    Write-Output "status_unavailable: cannot read $ProxyUrl/api/status or $ProxyUrl/models"
    return
  }

  Write-Output "=== subclaw / claw-proxy status ==="
  Write-Output ("time: {0}" -f (Get-Date -Format "HH:mm:ss"))
  Write-Output ("proxy: {0} | uptime={1}s | requests={2} | retries={3}" -f $ProxyUrl, $status.uptime_seconds, $status.requests, $status.compat_layer.upstream_retries)
  Write-Output ("keys: {0}/{1} real | sessions={2} | recent_routes={3}" -f $status.key_pool.real, $status.key_pool.total, (($status.sessions.PSObject.Properties | Measure-Object).Count), (@($status.recent_routes).Count))
  Write-Output ""

  Write-Output "Models:"
  foreach ($m in @($models.data)) {
    $caps = ($m.capabilities -join ",")
    $capacity = if ($null -ne $m.key_count) { $m.key_count } elseif ($null -ne $m.capacity) { $m.capacity } else { 0 }
    $overflow = if ($null -ne $m.overflow_keys) { $m.overflow_keys } else { 0 }
    Write-Output ("  - {0} | tier={1} | capacity={2} | overflow={3} | caps={4}" -f $m.id, $m.tier, $capacity, $overflow, $caps)
  }
  Write-Output ""

  Write-Output "Key pool:"
  foreach ($k in @($status.key_pool.keys)) {
    $modelList = if ($k.models) { $k.models -join "," } else { "*" }
    Write-Output ("  #{0} ...{1} | {2}/{3} | req={4} fail={5} | {6}" -f $k.index, $k.suffix, $k.protocol, $k.tier, $k.requests, $k.failures, $modelList)
  }
  Write-Output ""

  Write-Output "Recent routes:"
  foreach ($r in @($status.recent_routes | Select-Object -First $Recent)) {
    $ts = if ($r.ts) { ([DateTimeOffset]::FromUnixTimeSeconds([int64]$r.ts)).ToLocalTime().ToString("HH:mm:ss") } else { "--:--:--" }
    Write-Output ("  {0} | {1} | {2} | {3} | key=...{4} | session={5}" -f $ts, $r.client, $r.path, $r.status, $r.key_suffix, $r.session_id)
  }

  # Orchestration layer (best-effort): judge round + latest verdicts.
  if ($status.orchestration_enabled) {
    $orch = Get-Json "$ProxyUrl/orchestration"
    if ($orch -and $orch.enabled) {
      Write-Output ""
      $o = $orch.orchestrator
      if ($o) {
        Write-Output ("Orchestration: model={0} | running={1} | msg={2}" -f $o.model, $o.running, $o.msg)
      }
      Write-Output ("Judge gate: round={0}/{1}" -f $orch.judge_round, $orch.judge_cap)
      $judges = @($orch.judge_verdicts | Select-Object -First 5)
      if ($judges.Count -gt 0) {
        Write-Output "Latest judge verdicts:"
        foreach ($j in $judges) {
          $jt = if ($j.mtime) { ([DateTimeOffset]::FromUnixTimeSeconds([int64]$j.mtime)).ToLocalTime().ToString("HH:mm:ss") } else { "--:--:--" }
          Write-Output ("  {0} | {1} | {2}" -f $jt, $j.verdict, $j.file)
        }
      } else {
        Write-Output "Latest judge verdicts: (none yet)"
      }
    }
  }

  if ($ReportsDir -and (Test-Path -LiteralPath $ReportsDir)) {
    Write-Output ""
    Write-Output "Reports:"
    $reports = Get-ChildItem -LiteralPath $ReportsDir -Include "*.claw.*.md","*.codexclaw.*.md" -File -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First $Recent
    foreach ($file in $reports) {
      $exitLine = Select-String -LiteralPath $file.FullName -Pattern '^\[EXIT\]' -ErrorAction SilentlyContinue | Select-Object -Last 1
      $state = if ($exitLine) { $exitLine.Line } else { "running/no-exit-yet" }
      $markerPrefix = '^\s*(?:[#*-]+\s*)?'
      $progress = Select-String -LiteralPath $file.FullName -Pattern "$markerPrefix\[PROGRESS\]" -ErrorAction SilentlyContinue | Select-Object -Last 1
      $asks = @(Select-String -LiteralPath $file.FullName -Pattern "$markerPrefix\[ASK_ORCHESTRATOR\]" -ErrorAction SilentlyContinue)
      $claims = @(Select-String -LiteralPath $file.FullName -Pattern "$markerPrefix\[CLAIM\]" -ErrorAction SilentlyContinue)
      $evidence = @(Select-String -LiteralPath $file.FullName -Pattern "$markerPrefix\[EVIDENCE\]" -ErrorAction SilentlyContinue)
      $progText = if ($progress) { $progress.Line.Trim() } else { "no-progress-marker" }
      Write-Output ("  {0} | {1} | {2} | claims={3} evidence={4} asks={5}" -f $file.Name, $file.LastWriteTime.ToString("HH:mm:ss"), $state, $claims.Count, $evidence.Count, $asks.Count)
      Write-Output ("    progress: {0}" -f $progText)
      foreach ($ask in ($asks | Select-Object -Last 3)) {
        Write-Output ("    ask: {0}" -f $ask.Line.Trim())
      }
    }
  }
}

if (-not $Watch) {
  Render-Snapshot
  exit 0
}

$started = Get-Date
$last = ""
while ($true) {
  $snapshot = & {
    Render-Snapshot
  } | Out-String
  if ($snapshot.Trim() -and $snapshot -ne $last) {
    Write-Output $snapshot.TrimEnd()
    Write-Output ""
    $last = $snapshot
  }
  if ($MaxSeconds -gt 0 -and ((Get-Date) - $started).TotalSeconds -ge $MaxSeconds) { break }
  Start-Sleep -Seconds $IntervalSec
}

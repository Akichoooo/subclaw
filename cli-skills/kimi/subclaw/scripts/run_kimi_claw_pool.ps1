param(
  [Parameter(Mandatory=$true)][string]$Workdir,
  [string]$BriefDir = "",
  [string[]]$Task = @(),
  [string]$OutDir = "",
  [string]$Model = "",
  [int]$Jobs = 2,
  [int]$TimeoutSec = 900,
  [int]$MaxRetries = 2,
  [int]$StuckSec = 120,
  [string]$ProxyUrl = "http://127.0.0.1:4748",
  [string]$KimiCmd = ""
)

# subclaw kimi worker pool - streaming JSONL echo runner.
# ROUTING NOTE: this script only SPAWNS kimi CLI processes as external workers.
# It never touches other engines' native subagent mechanisms.

$ErrorActionPreference = "Stop"

if (-not $KimiCmd) {
  $cmd = Get-Command "kimi" -ErrorAction SilentlyContinue
  if (-not $cmd) { $cmd = Get-Command "kimi.cmd" -ErrorAction SilentlyContinue }
  if (-not $cmd) { throw "Kimi CLI not found on PATH. Pass -KimiCmd <path-to-kimi>." }
  $KimiCmd = $cmd.Source
}

function Write-JsonFile($Path, $Obj) {
  $tmp = "$Path.tmp"
  $Obj | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $tmp -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Read-Utf8($Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return "" }
  return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Update-PoolStatus($ReportsDir, $Stamp, $ModelName, $Message, [bool]$Running) {
  $workers = @()
  Get-ChildItem -LiteralPath $ReportsDir -Filter "worker_*.status.json" -File -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object {
      try { $workers += (Read-Utf8 $_.FullName) | ConvertFrom-Json } catch {}
    }
  Write-JsonFile (Join-Path $ReportsDir "pool_status.$Stamp.json") @{
    orchestrator = @{
      model = $ModelName
      msg = $Message
      running = $Running
      elapsed = [int]((Get-Date) - $script:StartTime).TotalSeconds
    }
    workers = $workers
  }
}

function Get-MarkerState([string]$Text) {
  $result = @{ progress = "running"; claims = 0; evidence = 0; asks = 0; lastAsk = ""; done = ""; doneStatus = "" }
  if (-not $Text) { return $result }
  foreach ($line in ($Text -split "`r?`n")) {
    if ($line -match '\[PROGRESS\]') { $result.progress = $line.Trim() }
    if ($line -match '\[CLAIM\]') { $result.claims += 1 }
    if ($line -match '\[EVIDENCE\]') { $result.evidence += 1 }
    if ($line -match '\[ASK_ORCHESTRATOR\]') { $result.asks += 1; $result.lastAsk = $line.Trim() }
    if ($line -match '\[WORKER_DONE\]') { $result.done = "yes" }
    if ($line -match 'status:\s*(OK|PARTIAL|FAIL)') { $result.doneStatus = $Matches[1] }
  }
  return $result
}

function Get-StreamText([string]$Path) {
  # Parse kimi stream-json JSONL, accumulate assistant text + tool-call events.
  $sb = New-Object System.Text.StringBuilder
  $tools = New-Object System.Collections.Generic.List[string]
  if (Test-Path -LiteralPath $Path) {
    foreach ($raw in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
      $line = $raw.Trim()
      if (-not $line) { continue }
      if (-not $line.StartsWith("{")) { [void]$sb.AppendLine($line); continue }  # text-mode fallback
      try { $ev = $line | ConvertFrom-Json } catch { continue }
      # defensive across possible schema shapes
      $role = $null
      if ($ev.PSObject.Properties.Name -contains "role") { $role = $ev.role }
      elseif ($ev.PSObject.Properties.Name -contains "type") { $role = $ev.type }
      $content = $null
      if ($ev.PSObject.Properties.Name -contains "content") { $content = $ev.content }
      elseif ($ev.PSObject.Properties.Name -contains "text") { $content = $ev.text }
      elseif ($ev.PSObject.Properties.Name -contains "message") {
        $m = $ev.message
        if ($m -and $m.PSObject.Properties.Name -contains "content") { $content = $m.content }
      }
      if ($ev.PSObject.Properties.Name -contains "tool_calls") {
        foreach ($tc in @($ev.tool_calls)) {
          if ($tc -and $tc.function -and $tc.function.name) { $tools.Add($tc.function.name) }
        }
      }
      if ($content -is [string] -and $content) { [void]$sb.AppendLine($content) }
    }
  }
  return @{ Text = $sb.ToString(); ToolCount = $tools.Count; LastTool = ($(if ($tools.Count) { $tools[$tools.Count - 1] } else { "" })) }
}

if (-not (Test-Path -LiteralPath $Workdir)) { throw "Workdir not found: $Workdir" }
if ($BriefDir) {
  if (-not (Test-Path -LiteralPath $BriefDir)) { throw "BriefDir not found: $BriefDir" }
  $Task += Get-ChildItem -LiteralPath $BriefDir -Filter "*.md" -File | Sort-Object Name | Select-Object -ExpandProperty FullName
}
if ($Task.Count -eq 0) { throw "No task briefs. Pass -BriefDir or -Task." }
if (-not $OutDir) {
  $agentDir = Join-Path $Workdir ".ai_agents"
  if (Test-Path -LiteralPath $agentDir) { $OutDir = Join-Path $agentDir "reports" }
  else { $OutDir = Join-Path (Get-Location) "kimi-claw-reports" }
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$models = Invoke-RestMethod -Uri "$ProxyUrl/models" -TimeoutSec 10
if (-not $Model) {
  if ($models.default_model) {
    $Model = $models.default_model
  } elseif (@($models.data).Count -gt 0) {
    $Model = @($models.data)[0].id
  } else {
    throw "No models reported by claw-proxy: $ProxyUrl/models"
  }
}
$modelInfo = @($models.data | Where-Object { $_.id -eq $Model })[0]
if (-not $modelInfo) { throw "Model not available through claw-proxy: $Model" }
$capacity = if ($null -ne $modelInfo.key_count) { [int]$modelInfo.key_count } elseif ($null -ne $modelInfo.capacity) { [int]$modelInfo.capacity } else { 0 }
if ($capacity -gt 0 -and $Jobs -gt $capacity) {
  Write-Output "warning: Jobs=$Jobs exceeds reported capacity=$capacity for $Model"
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$script:StartTime = Get-Date
Write-Output "kimi-claw pool: tasks=$($Task.Count), jobs=$Jobs, model=$Model, out=$OutDir"
Write-Output "kimi worker mode: -p --output-format stream-json (live echo) | proxy: $ProxyUrl/v1"

$queue = [System.Collections.Queue]::new()
foreach ($t in $Task) { $queue.Enqueue($t) }
$running = @()
$idx = 0

while ($queue.Count -gt 0 -or $running.Count -gt 0) {
  while ($queue.Count -gt 0 -and $running.Count -lt $Jobs) {
    $idx += 1
    $taskPath = [string]$queue.Dequeue()
    $base = [IO.Path]::GetFileNameWithoutExtension($taskPath)
    $report = Join-Path $OutDir "$base.kimiclaw.$Stamp.md"
    $statusPath = Join-Path $OutDir ("worker_{0:000}.status.json" -f $idx)
    $sid = "kimi-pool-$Stamp-$base-w$idx"
    $prompt = Read-Utf8 $taskPath
    $workerPrompt = @"
You are a subclaw worker running under Kimi Code. Follow the brief exactly. Read-only by default: do not modify files unless the brief explicitly says so.

Required report markers (emit them as plain lines in your reply):
- [PROGRESS] <short current step>   (max 50 total, then converge)
- [EVIDENCE] <file>:<line> - <fact>
- [CLAIM] <conclusion> | evidence: <file:line list> | confidence: high|medium|low
- [RISK] <risk or uncertainty>
- [ASK_ORCHESTRATOR] <question> only if blocked
- [WORKER_DONE] status: OK|PARTIAL|FAIL

Return a concise evidence packet (<= 2K tokens), not a transcript.

$prompt
"@
    Write-JsonFile $statusPath @{ model=$Model; msg="$base starting"; running=$true; status="RUNNING"; elapsed=0 }
    Set-Content -LiteralPath $report -Encoding UTF8 -Value "[META]`ntask: $taskPath`nmodel: $Model`nengine: kimi-cli`nendpoint: $ProxyUrl/v1`nsession: $sid`nstarted: $(Get-Date -Format o)`n[/META]`n`n[OUTPUT]"

    $promptFile = "$report.prompt"
    [System.IO.File]::WriteAllText($promptFile, $workerPrompt, (New-Object System.Text.UTF8Encoding($false)))
    $cliArgs = @("-p", "--output-format", "stream-json")
    $outFile = "$report.stream.jsonl"
    $errFile = "$report.stderr"
    if (Test-Path -LiteralPath $outFile) { Remove-Item -LiteralPath $outFile -Force }
    # ROUTING/SAFETY: clear OPENAI_* env vars so kimi reads config.toml providers, not shell overrides.
    $envBackup = @{ base = $env:OPENAI_BASE_URL; key = $env:OPENAI_API_KEY }
    $env:OPENAI_BASE_URL = $null
    $env:OPENAI_API_KEY = $null
    $proc = Start-Process -FilePath $KimiCmd -ArgumentList $cliArgs -WorkingDirectory $Workdir -WindowStyle Hidden -RedirectStandardInput $promptFile -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
    $env:OPENAI_BASE_URL = $envBackup.base
    $env:OPENAI_API_KEY = $envBackup.key
    $running += [pscustomobject]@{ Process=$proc; Index=$idx; Base=$base; Report=$report; StatusPath=$statusPath; OutFile=$outFile; ErrFile=$errFile; Started=Get-Date; LastGrow=Get-Date; LastLen=0; Attempt=1; TaskPath=$taskPath; PromptFile=$promptFile; Sid=$sid }
  }

  Start-Sleep -Seconds 2
  $next = @()
  foreach ($w in $running) {
    $elapsed = [int]((Get-Date) - $w.Started).TotalSeconds
    if ($w.Process.HasExited) {
      $parsed = Get-StreamText $w.OutFile
      $stderr = Read-Utf8 $w.ErrFile
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value $parsed.Text
      if ($stderr.Trim()) { Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[STDERR]`n$stderr`n[/STDERR]" }
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[/OUTPUT]`n[EXIT] code=$($w.Process.ExitCode) duration_sec=$elapsed"
      $markers = Get-MarkerState $parsed.Text
      $state = if ($w.Process.ExitCode -eq 0) { $(if ($markers.doneStatus -eq "FAIL") { "FAIL" } elseif ($markers.doneStatus -eq "PARTIAL") { "PARTIAL" } else { "OK" }) } else { "FAIL" }
      $retryable = ($w.Process.ExitCode -ne 0) -and ($w.Attempt -le $MaxRetries) -and ($state -eq "FAIL")
      if ($retryable) {
        Write-Output ("WORKER idx={0} attempt={1} failed (ec={2}), retrying..." -f $w.Index, $w.Attempt, $w.Process.ExitCode)
        Start-Sleep -Seconds (2 * $w.Attempt)
        $w.Attempt += 1
        $w.Started = Get-Date
        $w.LastGrow = Get-Date
        $w.LastLen = 0
        Write-JsonFile $w.StatusPath @{ model=$Model; msg="$($w.Base) retry $($w.Attempt)"; running=$true; status="RUNNING"; elapsed=0 }
        Set-Content -LiteralPath $w.Report -Encoding UTF8 -Value "[META]`ntask: $($w.TaskPath)`nmodel: $Model`nengine: kimi-cli`nendpoint: $ProxyUrl/v1`nsession: $($w.Sid)`nattempt: $($w.Attempt)`nstarted: $(Get-Date -Format o)`n[/META]`n`n[OUTPUT]"
        if (Test-Path -LiteralPath $w.OutFile) { Remove-Item -LiteralPath $w.OutFile -Force }
        $env:OPENAI_BASE_URL = $null
        $env:OPENAI_API_KEY = $null
        $w.Process = Start-Process -FilePath $KimiCmd -ArgumentList @("-p", "--output-format", "stream-json") -WorkingDirectory $Workdir -WindowStyle Hidden -RedirectStandardInput $w.PromptFile -RedirectStandardOutput $w.OutFile -RedirectStandardError $w.ErrFile -PassThru
        $env:OPENAI_BASE_URL = $envBackup.base
        $env:OPENAI_API_KEY = $envBackup.key
        $next += $w
      } else {
        Write-JsonFile $w.StatusPath @{ model=$Model; msg="Done ($($w.Base)) tools=$($parsed.ToolCount)"; running=$false; status=$state; elapsed=$elapsed }
        Write-Output ("WORKER idx={0} status={1} dur={2}s report={3}" -f $w.Index, $state, $elapsed, $w.Report)
      }
    } elseif ($elapsed -gt $TimeoutSec) {
      Stop-Process -Id $w.Process.Id -Force -ErrorAction SilentlyContinue
      $parsed = Get-StreamText $w.OutFile
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value $parsed.Text
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[/OUTPUT]`n[EXIT] code=124 duration_sec=$elapsed"
      Write-JsonFile $w.StatusPath @{ model=$Model; msg="Timed out ($($w.Base))"; running=$false; status="TIMEOUT"; elapsed=$elapsed }
      Write-Output ("WORKER idx={0} status=TIMEOUT dur={1}s report={2}" -f $w.Index, $elapsed, $w.Report)
    } else {
      # Live streaming echo: parse growing JSONL, mirror tool events + markers into status.json.
      $parsed = Get-StreamText $w.OutFile
      $curLen = if (Test-Path -LiteralPath $w.OutFile) { (Get-Item -LiteralPath $w.OutFile).Length } else { 0 }
      if ($curLen -gt $w.LastLen) { $w.LastLen = $curLen; $w.LastGrow = Get-Date }
      $idleSec = [int]((Get-Date) - $w.LastGrow).TotalSeconds
      $markers = Get-MarkerState $parsed.Text
      $msg = "{0} | tools={1} claims={2} evidence={3} asks={4}" -f $markers.progress, $parsed.ToolCount, $markers.claims, $markers.evidence, $markers.asks
      if ($markers.lastAsk) { $msg = "$msg | $($markers.lastAsk)" }
      $wstate = "RUNNING"
      if ($idleSec -gt $StuckSec) { $wstate = "STUCK"; $msg = "STUCK ${idleSec}s no output | $msg" }
      Write-JsonFile $w.StatusPath @{ model=$Model; msg=$msg; running=$true; status=$wstate; elapsed=$elapsed }
      $next += $w
    }
  }
  $running = $next
  Update-PoolStatus $OutDir $Stamp $Model "Dispatching $($Task.Count) tasks..." ($running.Count -gt 0 -or $queue.Count -gt 0)
}

Update-PoolStatus $OutDir $Stamp $Model "Pool complete" $false
Write-Output "POOL_DONE OUTDIR=$OutDir STAMP=$Stamp"

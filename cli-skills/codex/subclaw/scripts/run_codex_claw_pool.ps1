param(
  [Parameter(Mandatory=$true)][string]$Workdir,
  [string]$BriefDir = "",
  [string[]]$Task = @(),
  [string]$OutDir = "",
  [string]$Model = "",
  [int]$Jobs = 2,
  [int]$TimeoutSec = 900,
  [string]$ProxyUrl = "http://127.0.0.1:4748",
  [string]$CodexCmd = ""
)

$ErrorActionPreference = "Stop"

if (-not $CodexCmd) {
  $cmd = Get-Command "codex" -ErrorAction SilentlyContinue
  if (-not $cmd) { $cmd = Get-Command "codex.cmd" -ErrorAction SilentlyContinue }
  if (-not $cmd) { throw "Codex CLI not found on PATH. Pass -CodexCmd <path-to-codex>." }
  $CodexCmd = $cmd.Source
}

function Write-JsonFile($Path, $Obj) {
  $tmp = "$Path.tmp"
  $Obj | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $tmp -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Update-PoolStatus($ReportsDir, $Stamp, $Model, $Message, [bool]$Running) {
  $workers = @()
  Get-ChildItem -LiteralPath $ReportsDir -Filter "worker_*.status.json" -File -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object {
      try { $workers += Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } catch {}
    }
  Write-JsonFile (Join-Path $ReportsDir "pool_status.$Stamp.json") @{
    orchestrator = @{
      model = $Model
      msg = $Message
      running = $Running
      elapsed = [int]((Get-Date) - $script:StartTime).TotalSeconds
    }
    workers = $workers
  }
}

function Read-WorkerMarkers($Path) {
  $result = @{
    progress = "running"
    claims = 0
    evidence = 0
    asks = 0
    lastAsk = ""
  }
  if (-not (Test-Path -LiteralPath $Path)) { return $result }
  $lines = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue
  foreach ($line in $lines) {
    if ($line -match '\[PROGRESS\]') { $result.progress = $line.Trim() }
    if ($line -match '\[CLAIM\]') { $result.claims += 1 }
    if ($line -match '\[EVIDENCE\]') { $result.evidence += 1 }
    if ($line -match '\[ASK_ORCHESTRATOR\]') {
      $result.asks += 1
      $result.lastAsk = $line.Trim()
    }
  }
  return $result
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
  else { $OutDir = Join-Path (Get-Location) "codex-claw-reports" }
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
Write-Output "codex-claw pool: tasks=$($Task.Count), jobs=$Jobs, model=$Model, out=$OutDir"
Write-Output "codex base_url: $ProxyUrl/v1 | wire_api=responses"

$queue = [System.Collections.Queue]::new()
foreach ($t in $Task) { $queue.Enqueue($t) }
$running = @()
$idx = 0

while ($queue.Count -gt 0 -or $running.Count -gt 0) {
  while ($queue.Count -gt 0 -and $running.Count -lt $Jobs) {
    $idx += 1
    $taskPath = [string]$queue.Dequeue()
    $base = [IO.Path]::GetFileNameWithoutExtension($taskPath)
    $report = Join-Path $OutDir "$base.codexclaw.$Stamp.md"
    $statusPath = Join-Path $OutDir ("worker_{0:000}.status.json" -f $idx)
    $prompt = Get-Content -LiteralPath $taskPath -Raw
    $workerPrompt = @"
You are a Codex subclaw worker. Follow the brief exactly.

Required report markers:
- [PROGRESS] <short current step>
- [EVIDENCE] <file>:<line> - <fact>
- [CLAIM] <conclusion> | evidence: <file:line list> | confidence: high|medium|low
- [RISK] <risk or uncertainty>
- [ASK_ORCHESTRATOR] <question> only if blocked
- [WORKER_DONE] status: OK|PARTIAL|FAIL

Return a concise evidence packet, not a transcript.

$prompt
"@
    Write-JsonFile $statusPath @{ model=$Model; msg="$base starting"; running=$true; status="RUNNING"; elapsed=0 }
    Set-Content -LiteralPath $report -Encoding UTF8 -Value "[META]`ntask: $taskPath`nmodel: $Model`nengine: codex-cli`nendpoint: $ProxyUrl/v1`nstarted: $(Get-Date -Format o)`n[/META]`n`n[OUTPUT]"

    $promptFile = "$report.prompt"
    Set-Content -LiteralPath $promptFile -Encoding UTF8 -Value $workerPrompt
    $args = @(
      "exec",
      "-m", $Model,
      "--cd", $Workdir,
      "--skip-git-repo-check",
      "--sandbox", "read-only",
      "-c", 'model_provider="claw"',
      "-c", 'model_providers.claw.name="claw"',
      "-c", ('model_providers.claw.base_url="{0}/v1"' -f $ProxyUrl.TrimEnd('/')),
      "-c", 'model_providers.claw.wire_api="responses"',
      "-"
    )
    $outFile = "$report.stdout"
    $errFile = "$report.stderr"
    $proc = Start-Process -FilePath $CodexCmd -ArgumentList $args -WorkingDirectory $Workdir -WindowStyle Hidden -RedirectStandardInput $promptFile -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
    $running += [pscustomobject]@{ Process=$proc; Index=$idx; Base=$base; Report=$report; StatusPath=$statusPath; OutFile=$outFile; ErrFile=$errFile; Started=Get-Date }
  }

  Start-Sleep -Seconds 2
  $next = @()
  foreach ($w in $running) {
    $elapsed = [int]((Get-Date) - $w.Started).TotalSeconds
    if ($w.Process.HasExited) {
      $stdout = if (Test-Path -LiteralPath $w.OutFile) { Get-Content -LiteralPath $w.OutFile -Raw } else { "" }
      $stderr = if (Test-Path -LiteralPath $w.ErrFile) { Get-Content -LiteralPath $w.ErrFile -Raw } else { "" }
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value $stdout
      if ($stderr.Trim()) { Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[STDERR]`n$stderr`n[/STDERR]" }
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[/OUTPUT]`n[EXIT] code=$($w.Process.ExitCode) duration_sec=$elapsed"
      $state = if ($w.Process.ExitCode -eq 0) { "OK" } else { "FAIL" }
      Write-JsonFile $w.StatusPath @{ model=$Model; msg="Done ($($w.Base))"; running=$false; status=$state; elapsed=$elapsed }
      Write-Output ("WORKER idx={0} status={1} dur={2}s report={3}" -f $w.Index, $state, $elapsed, $w.Report)
    } elseif ($elapsed -gt $TimeoutSec) {
      Stop-Process -Id $w.Process.Id -Force -ErrorAction SilentlyContinue
      Add-Content -LiteralPath $w.Report -Encoding UTF8 -Value "`n[/OUTPUT]`n[EXIT] code=124 duration_sec=$elapsed"
      Write-JsonFile $w.StatusPath @{ model=$Model; msg="Timed out ($($w.Base))"; running=$false; status="TIMEOUT"; elapsed=$elapsed }
      Write-Output ("WORKER idx={0} status=TIMEOUT dur={1}s report={2}" -f $w.Index, $elapsed, $w.Report)
    } else {
      $markers = Read-WorkerMarkers $w.OutFile
      $msg = "{0} | claims={1} evidence={2} asks={3}" -f $markers.progress, $markers.claims, $markers.evidence, $markers.asks
      if ($markers.lastAsk) { $msg = "$msg | $($markers.lastAsk)" }
      Write-JsonFile $w.StatusPath @{ model=$Model; msg=$msg; running=$true; status="RUNNING"; elapsed=$elapsed }
      $next += $w
    }
  }
  $running = $next
  Update-PoolStatus $OutDir $Stamp $Model "Dispatching $($Task.Count) tasks..." ($running.Count -gt 0 -or $queue.Count -gt 0)
}

Update-PoolStatus $OutDir $Stamp $Model "Pool complete" $false
Write-Output "POOL_DONE OUTDIR=$OutDir STAMP=$Stamp"

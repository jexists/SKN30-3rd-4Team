[CmdletBinding()]
param(
    [int]$IntervalSeconds = 60,
    [int]$StatusIntervalSeconds = 600,
    [string]$Distribution = "Ubuntu-24.04"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WslRepoRoot = "/mnt/d/project/SKN30-3rd-4Team-dev"
$LogDir = Join-Path $RepoRoot "pipeline\logs\embedding"
$LogPath = Join-Path $LogDir "windows_watch_full_500.log"
$PidPath = Join-Path $LogDir "windows_watch_full_500.pid"
$EmbeddingManifestPattern = Join-Path $RepoRoot "data\legal_api_v2\08_embedding_500_ov50_*\manifest.json"
$GridCompletePath = Join-Path $RepoRoot "reports\legal_api_v2\eval\grid_complete.json"
$StatusScriptPath = Join-Path $RepoRoot "pipeline\status_report.py"
$ManifestValidationPath = Join-Path $RepoRoot "pipeline\validate_manifests.py"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$PID | Set-Content -Path $PidPath -Encoding ascii

function Write-WatchLog {
    param([string]$Message)
    $timestamp = [DateTimeOffset]::Now.ToString("o")
    Add-Content -Path $LogPath -Encoding utf8 -Value "$timestamp $Message"
}

function Test-WslExactCommand {
    param([string]$CommandLine)
    try {
        if ($CommandLine.Contains("'")) {
            throw "Single quotes are not allowed in the fixed WSL command probe."
        }
        & wsl -d $Distribution -- bash -lc "ps -eo args= | grep -Fqx -- '$CommandLine'" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Start-WslRunner {
    param([string]$ScriptPath)
    & wsl -d $Distribution -- bash -lc "cd '$WslRepoRoot' && setsid -f bash '$ScriptPath'"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL runner start failed: $ScriptPath (exit=$LASTEXITCODE)"
    }
    Write-WatchLog "STARTED $ScriptPath"
}

function Get-EvaluationCompletion {
    $manifests = @(
        Get-ChildItem -Path $EmbeddingManifestPattern -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -notlike "*_smoke*" }
    )
    if ($manifests.Count -lt 3) {
        return [pscustomobject]@{ EmbeddingsComplete = $false; EvaluationsComplete = $false }
    }
    $evaluationComplete = $true
    foreach ($manifestPath in $manifests) {
        try {
            $manifest = Get-Content -Path $manifestPath.FullName -Raw -Encoding utf8 | ConvertFrom-Json
            $summaryPath = Join-Path $RepoRoot "reports\legal_api_v2\eval\$($manifest.experiment_id)\experiment_summary.csv"
            if (-not (Test-Path -LiteralPath $summaryPath)) {
                $evaluationComplete = $false
            }
        }
        catch {
            $evaluationComplete = $false
        }
    }
    return [pscustomobject]@{ EmbeddingsComplete = $true; EvaluationsComplete = $evaluationComplete }
}

function Update-StatusReport {
    try {
        & python $ManifestValidationPath *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "validate_manifests.py exit=$LASTEXITCODE"
        }
        & python $StatusScriptPath *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "status_report.py exit=$LASTEXITCODE"
        }
        Write-WatchLog "MANIFEST_VALIDATED STATUS_UPDATED"
    }
    catch {
        Write-WatchLog "STATUS_UPDATE_FAILED error=$($_.Exception.Message)"
    }
}

Write-WatchLog "WATCH_STARTED pid=$PID interval_seconds=$IntervalSeconds status_interval_seconds=$StatusIntervalSeconds"
$lastStatusUpdate = [DateTimeOffset]::MinValue
try {
    while ($true) {
        $now = [DateTimeOffset]::Now
        if (($now - $lastStatusUpdate).TotalSeconds -ge $StatusIntervalSeconds) {
            Update-StatusReport
            $lastStatusUpdate = $now
        }
        if (Test-Path -LiteralPath $GridCompletePath) {
            Write-WatchLog "WATCH_COMPLETE provisional_grid=true"
            break
        }
        $completion = Get-EvaluationCompletion
        if (-not $completion.EmbeddingsComplete) {
            $embeddingRunner = Test-WslExactCommand "bash pipeline/embedding/run_full_500_models.sh"
            if (-not $embeddingRunner) {
                try {
                    Start-WslRunner "pipeline/embedding/run_full_500_models.sh"
                }
                catch {
                    Write-WatchLog "START_FAILED embedding=$($_.Exception.Message)"
                }
            }
        }

        if (-not $completion.EvaluationsComplete) {
            $evaluationRunner = Test-WslExactCommand "bash pipeline/evaluation/run_after_500_embeddings.sh"
            if (-not $evaluationRunner) {
                try {
                    Start-WslRunner "pipeline/evaluation/run_after_500_embeddings.sh"
                }
                catch {
                    Write-WatchLog "START_FAILED evaluation=$($_.Exception.Message)"
                }
            }
        }
        else {
            $gridRunner = Test-WslExactCommand "bash pipeline/embedding/run_grid_after_500.sh"
            if (-not $gridRunner) {
                try {
                    Start-WslRunner "pipeline/embedding/run_grid_after_500.sh"
                }
                catch {
                    Write-WatchLog "START_FAILED grid=$($_.Exception.Message)"
                }
            }
        }

        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {
    if (Test-Path -LiteralPath $PidPath) {
        Remove-Item -LiteralPath $PidPath -Force
    }
    Write-WatchLog "WATCH_STOPPED pid=$PID"
}

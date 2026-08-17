param(
    [string]$RunId = "",
    [ValidateRange(1, 300)]
    [int]$IntervalSeconds = 2,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$experimentsRoot = Join-Path $projectRoot "experiments"

function Write-StateLine {
    param(
        [string]$Label,
        [string]$Value,
        [ConsoleColor]$Color = [ConsoleColor]::Gray
    )
    Write-Host (("{0,-12} {1}" -f ($Label + ":"), $Value)) -ForegroundColor $Color
}

function Test-ProcessAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-ExperimentStatusFiles {
    param([string]$SelectedRunId)

    if (-not (Test-Path -LiteralPath $experimentsRoot)) { return @() }
    $searchRoot = if ($SelectedRunId) {
        Join-Path $experimentsRoot $SelectedRunId
    } else {
        $experimentsRoot
    }
    if (-not (Test-Path -LiteralPath $searchRoot)) { return @() }

    return @(Get-ChildItem -LiteralPath $searchRoot -Filter "runtime_status.json" -File -Recurse |
        Where-Object { $_.FullName -notmatch '[\\/]attempts[\\/]' } |
        Sort-Object LastWriteTime -Descending)
}

function Show-BackendStatus {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/settings/providers" -TimeoutSec 2
        Write-StateLine "后端" "正常（127.0.0.1:8000）" Green
    } catch {
        Write-StateLine "后端" "无法连接（实验运行前请先启动后端）" Red
    }
}

function Show-ExperimentStatus {
    param([System.IO.FileInfo]$StatusFile)

    try {
        $status = Get-Content -LiteralPath $StatusFile.FullName -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        Write-StateLine "状态文件" "读取失败：$($StatusFile.FullName)" Red
        Write-StateLine "异常" $_.Exception.Message Red
        return
    }

    $pidValue = if ($null -ne $status.pid) { [int]$status.pid } else { 0 }
    $alive = Test-ProcessAlive -ProcessId $pidValue
    $updatedAt = $null
    $heartbeatAge = $null
    try {
        $updatedAt = [DateTimeOffset]::Parse([string]$status.updated_at)
        $heartbeatAge = [Math]::Max(0, ([DateTimeOffset]::UtcNow - $updatedAt.ToUniversalTime()).TotalSeconds)
    } catch {}

    $stateColor = if ($status.state -eq "running" -and $alive -and $heartbeatAge -le 15) {
        [ConsoleColor]::Green
    } elseif ($alive -or $status.state -in @("orphaned", "stalled")) {
        [ConsoleColor]::Red
    } else {
        [ConsoleColor]::Yellow
    }

    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-StateLine "Run" ([string]$status.run_id) Cyan
    Write-StateLine "Experiment" ("{0} / {1}" -f $status.experiment_id, $status.attempt_id) Cyan
    Write-StateLine "状态" ([string]$status.state) $stateColor
    Write-StateLine "PID" ("{0}（{1}）" -f $pidValue, $(if ($alive) { "存活" } else { "不存在" })) $(if ($alive) { "Green" } else { "Yellow" })

    if ($heartbeatAge -ne $null) {
        $heartbeatColor = if ($heartbeatAge -le 15) { "Green" } elseif ($alive) { "Red" } else { "Yellow" }
        Write-StateLine "状态更新" ("{0:N1} 秒前（{1}）" -f $heartbeatAge, $updatedAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")) $heartbeatColor
    } else {
        Write-StateLine "状态更新" "时间格式无效" Red
    }

    if ($alive) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
        $owned = $process.CommandLine -like "*--run-id $($status.run_id)*" -and
            $process.CommandLine -like "*--experiment-id $($status.experiment_id)*"
        Write-StateLine "PID归属" $(if ($owned) { "命令行与当前实验匹配" } else { "不匹配，可能发生 PID 复用" }) $(if ($owned) { "Green" } else { "Red" })
        if ($process) {
            Write-StateLine "命令" ([string]$process.CommandLine) DarkGray
        }
    }

    $logPath = [string]$status.log_path
    $resultPath = [string]$status.result_path
    $logSize = if ($logPath -and (Test-Path -LiteralPath $logPath)) { (Get-Item -LiteralPath $logPath).Length } else { 0 }
    $resultExists = $resultPath -and (Test-Path -LiteralPath $resultPath)
    Write-StateLine "日志" ("{0} bytes · {1}" -f $logSize, $logPath) $(if ($alive -and $logSize -eq 0 -and $heartbeatAge -gt 15) { "Red" } else { "Gray" })
    if ($logPath -and (Test-Path -LiteralPath $logPath)) {
        $epochEvent = Get-Content -LiteralPath $logPath -Tail 200 -Encoding utf8 -ErrorAction SilentlyContinue |
            ForEach-Object {
                try { $_ | ConvertFrom-Json -ErrorAction Stop } catch { $null }
            } |
            Where-Object { $_.event -eq "epoch_end" } |
            Select-Object -Last 1
        if ($epochEvent) {
            $variant = if ($epochEvent.variant) { [string]$epochEvent.variant } else { "-" }
            $seed = if ($null -ne $epochEvent.seed) { [string]$epochEvent.seed } else { "-" }
            $loss = if ($null -ne $epochEvent.loss) { [string]$epochEvent.loss } else { "-" }
            Write-StateLine "训练进度" ("variant={0} · seed={1} · epoch={2}/{3} · loss={4}" -f $variant, $seed, $epochEvent.epoch, $epochEvent.total_epochs, $loss) Green
        } elseif ($alive) {
            Write-StateLine "训练进度" "尚未收到 epoch_end 事件" Yellow
        }
    }
    Write-StateLine "结果" $(if ($resultExists) { "已生成：$resultPath" } else { "尚未生成" }) $(if ($resultExists) { "Green" } else { "Yellow" })

    $experimentDir = $StatusFile.Directory.FullName
    $tempFiles = @(Get-ChildItem -LiteralPath $experimentDir -Filter "*runtime_status*.tmp" -File -Force -ErrorAction SilentlyContinue)
    $lockFile = Join-Path $experimentDir ".experiment.lock"
    Write-StateLine "临时文件" $(if ($tempFiles.Count) { "发现状态临时文件（状态替换可能异常）" } else { "无" }) $(if ($tempFiles.Count) { "Red" } else { "Green" })
    Write-StateLine "运行锁" $(if (Test-Path -LiteralPath $lockFile) { "存在" } else { "无" }) $(if ($alive -and (Test-Path -LiteralPath $lockFile)) { "Green" } elseif (Test-Path -LiteralPath $lockFile) { "Red" } else { "Gray" })

    if ($alive -and $heartbeatAge -gt 15) {
        Write-Host "警告：实验进程仍存在，但后台状态已停止更新。" -ForegroundColor Red
    }
}

do {
    Clear-Host
    Write-Host "实验进程检查器 chenck6" -ForegroundColor Cyan
    Write-Host ("检查时间：{0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -ForegroundColor DarkGray
    Show-BackendStatus

    $statusFiles = Get-ExperimentStatusFiles -SelectedRunId $RunId
    if ($statusFiles.Count -eq 0) {
        $scope = if ($RunId) { $RunId } else { "全部 run" }
        Write-StateLine "实验状态" "未找到（范围：$scope）" Yellow
    } else {
        foreach ($statusFile in $statusFiles) {
            Show-ExperimentStatus -StatusFile $statusFile
        }
    }

    if (-not $Once) {
        Write-Host ""
        Write-Host ("每 {0} 秒刷新；按 Ctrl+C 退出。" -f $IntervalSeconds) -ForegroundColor DarkGray
        Start-Sleep -Seconds $IntervalSeconds
    }
} while (-not $Once)

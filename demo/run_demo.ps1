param(
    [ValidateRange(0, 100)]
    [double]$TimeScale = 1,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$ApiKey = "highground-local-demo-key",
    [string]$Output = "demo/artifacts/latest-evidence.json"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
$resolvedOutput = if ([System.IO.Path]::IsPathRooted($Output)) {
    [System.IO.Path]::GetFullPath($Output)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Output))
}
$apiUrl = "http://127.0.0.1:$Port"
$temporaryRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("xpeng-highground-demo-" + [System.Guid]::NewGuid().ToString("N"))
$databasePath = Join-Path $temporaryRoot "highground.db"
$serverStdoutPath = Join-Path $temporaryRoot "uvicorn.stdout.log"
$serverStderrPath = Join-Path $temporaryRoot "uvicorn.stderr.log"
$server = $null
$environmentNames = @(
    "HIGHGROUND_DATABASE_PATH",
    "HIGHGROUND_API_KEY",
    "HIGHGROUND_ENV",
    "HIGHGROUND_ACTUATOR_MODE",
    "HIGHGROUND_AUTH_TTL_SECONDS",
    "HIGHGROUND_EVENT_MAX_AGE_SECONDS",
    "HIGHGROUND_ALLOWED_ORIGINS",
    "PYTHONUTF8"
)
$savedEnvironment = @{}

foreach ($name in $environmentNames) {
    $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    $savedEnvironment[$name] = [PSCustomObject]@{
        Exists = $null -ne $item
        Value = if ($null -ne $item) { $item.Value } else { $null }
    }
}

function Get-ServerDiagnostics {
    $messages = @()
    foreach ($path in @($serverStdoutPath, $serverStderrPath)) {
        if (Test-Path -LiteralPath $path) {
            $content = Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue
            if ($null -ne $content) {
                $content = $content.Trim()
                if (-not [string]::IsNullOrWhiteSpace($content)) {
                    $messages += $content
                }
            }
        }
    }
    return ($messages -join " | ")
}

try {
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null

    $env:HIGHGROUND_DATABASE_PATH = $databasePath
    $env:HIGHGROUND_API_KEY = $ApiKey
    $env:HIGHGROUND_ENV = "demo"
    $env:HIGHGROUND_ACTUATOR_MODE = "record-only"
    $env:HIGHGROUND_AUTH_TTL_SECONDS = "120"
    $env:HIGHGROUND_EVENT_MAX_AGE_SECONDS = "300"
    $env:HIGHGROUND_ALLOWED_ORIGINS = "$apiUrl,http://localhost:$Port"
    $env:PYTHONUTF8 = "1"

    Write-Output "Temporary demo database: $databasePath"

    $portProbe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )
    try {
        $portProbe.Start()
    } catch {
        throw "Port $Port is already in use on 127.0.0.1."
    } finally {
        $portProbe.Stop()
    }

    $startProcess = @{
        FilePath = $python
        ArgumentList = @(
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            $Port
        )
        WorkingDirectory = $repoRoot
        RedirectStandardOutput = $serverStdoutPath
        RedirectStandardError = $serverStderrPath
        PassThru = $true
    }
    if ($env:OS -eq "Windows_NT") {
        $startProcess.WindowStyle = "Hidden"
    }
    $server = Start-Process @startProcess

    $healthy = $false
    foreach ($attempt in 1..40) {
        if ($server.HasExited) {
            $server.WaitForExit()
            $diagnostics = Get-ServerDiagnostics
            throw (
                "FastAPI exited before becoming healthy (exit code $($server.ExitCode)). " +
                "Server output: $diagnostics"
            )
        }
        try {
            $health = Invoke-RestMethod -Uri "$apiUrl/healthz" -TimeoutSec 1
            if ($health.status -eq "ok" -and $health.actuator_mode -eq "record-only") {
                $healthy = $true
                break
            }
        } catch {}
        if (-not $healthy) { Start-Sleep -Milliseconds 250 }
    }
    if (-not $healthy) {
        $diagnostics = Get-ServerDiagnostics
        throw "FastAPI did not become healthy at $apiUrl. Server output: $diagnostics"
    }

    & $python (Join-Path $repoRoot "demo/run_scenario.py") `
        --api-url $apiUrl `
        --api-key $ApiKey `
        --time-scale $TimeScale `
        --output $resolvedOutput
    if ($LASTEXITCODE -ne 0) {
        throw "The canonical demo scenario failed."
    }
} finally {
    if ($null -ne $server) {
        try {
            if (-not $server.HasExited) {
                Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
            }
            $server.WaitForExit()
        } catch {
            # The process can exit between HasExited and Stop-Process.
        } finally {
            $server.Dispose()
        }
    }

    foreach ($name in $environmentNames) {
        $saved = $savedEnvironment[$name]
        if ($saved.Exists) {
            Set-Item -LiteralPath "Env:$name" -Value $saved.Value
        } else {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

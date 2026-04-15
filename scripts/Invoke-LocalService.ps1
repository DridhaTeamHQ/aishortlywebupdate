param(
  [Parameter(Mandatory = $true)]
  [string]$ServiceName,

  [Parameter(Mandatory = $true)]
  [string]$Workdir,

  [Parameter(Mandatory = $true)]
  [string]$Executable,

  [Parameter(Mandatory = $false)]
  [string[]]$Arguments = @(),

  [Parameter(Mandatory = $true)]
  [string]$LogPath
)

function Import-DotEnvFile {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) {
      return
    }

    $parts = $line -split '=', 2
    if ($parts.Count -ne 2) {
      return
    }

    $name = $parts[0].Trim()
    $value = $parts[1].Trim()

    if (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}

$root = Resolve-Path (Join-Path $PSScriptRoot '..')

Import-DotEnvFile (Join-Path $root '.env')
Import-DotEnvFile (Join-Path $root 'frontend\.env.local')

if (-not $env:BROWSER_STREAM_BASE_URL) {
  $env:BROWSER_STREAM_BASE_URL = 'http://localhost:8090'
}

if (-not $env:REDIS_URL) {
  $env:REDIS_URL = 'redis://localhost:6379/0'
}

if (-not $env:RQ_QUEUE_NAME) {
  $env:RQ_QUEUE_NAME = 'agent-runs'
}

if (-not $env:AI_AGENT_REPO_PATH) {
  $defaultRepo = Join-Path $root 'external\ai-agent-browser'
  if (Test-Path -LiteralPath $defaultRepo) {
    $env:AI_AGENT_REPO_PATH = $defaultRepo
  }
}

$activateScript = Join-Path $root '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $activateScript) {
  . $activateScript
}

Set-Location -LiteralPath $Workdir

Write-Host ""
Write-Host "=== $ServiceName ===" -ForegroundColor Cyan
Write-Host "Working directory: $Workdir"
Write-Host "Executable: $Executable"
if ($Arguments.Count -gt 0) {
  Write-Host "Arguments: $($Arguments -join ' ')"
}
Write-Host "Log file: $LogPath"
Write-Host ""

$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $logDir)) {
  New-Item -ItemType Directory -Path $logDir | Out-Null
}

try {
  & $Executable @Arguments *>> $LogPath
}
catch {
  $_ | Out-String | Tee-Object -FilePath $LogPath -Append
  Write-Host ""
  Write-Host "$ServiceName crashed. Check the log above or at $LogPath" -ForegroundColor Red
  Read-Host "Press Enter to close"
  exit 1
}

if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "$ServiceName exited with code $LASTEXITCODE. Check $LogPath" -ForegroundColor Yellow
  Read-Host "Press Enter to close"
  exit $LASTEXITCODE
}

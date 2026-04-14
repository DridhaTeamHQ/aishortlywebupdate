$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$runner = Join-Path $PSScriptRoot 'Invoke-LocalService.ps1'
$bootstrap = Join-Path $PSScriptRoot 'Ensure-Local-BackendEnv.ps1'
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$logDir = Join-Path $root 'artifacts\local-logs'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

& $bootstrap

$services = @(
  @{
    Name = 'Shortly API'
    Workdir = $root.Path
    Executable = $venvPython
    Arguments = @('-m', 'uvicorn', 'platform.api.app.main:app', '--host', '0.0.0.0', '--port', '8000')
    LogPath = (Join-Path $logDir 'api.log')
  },
  @{
    Name = 'Shortly Worker'
    Workdir = $root.Path
    Executable = $venvPython
    Arguments = @('worker.py')
    LogPath = (Join-Path $logDir 'worker.log')
  },
  @{
    Name = 'Browser Stream'
    Workdir = $root.Path
    Executable = $venvPython
    Arguments = @('-m', 'uvicorn', 'platform.browser-stream.main:app', '--host', '0.0.0.0', '--port', '8090')
    LogPath = (Join-Path $logDir 'browser-stream.log')
  }
)

foreach ($service in $services) {
  $argListLiteral = ($service.Arguments | ForEach-Object { "'$_'" }) -join ', '
  Start-Process -FilePath $powershell -WorkingDirectory $service.Workdir -ArgumentList @(
    '-NoExit',
    '-ExecutionPolicy', 'Bypass',
    '-Command', "& '$runner' -ServiceName '$($service.Name)' -Workdir '$($service.Workdir)' -Executable '$($service.Executable)' -Arguments @($argListLiteral) -LogPath '$($service.LogPath)'"
  ) | Out-Null
}

Write-Host ""
Write-Host "Backend services are starting in separate windows." -ForegroundColor Green
Write-Host "API: http://localhost:8000"
Write-Host "Browser stream: http://localhost:8090"
Write-Host "Logs: $logDir"
Write-Host ""

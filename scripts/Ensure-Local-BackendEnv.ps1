$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Find-SystemPython {
  if ($env:PYTHON_EXE -and (Test-Path -LiteralPath $env:PYTHON_EXE)) {
    return $env:PYTHON_EXE
  }

  $candidates = @(
    (Get-Command py -ErrorAction SilentlyContinue)?.Source,
    (Get-Command python -ErrorAction SilentlyContinue)?.Source,
    (Get-Command python3 -ErrorAction SilentlyContinue)?.Source,
    'C:\Users\Tamada\AppData\Local\Programs\Python\Python313\python.exe',
    'C:\Users\Tamada\AppData\Local\Programs\Python\Python312\python.exe',
    'C:\Users\Tamada\AppData\Local\Programs\Python\Python311\python.exe',
    'C:\Program Files\Python313\python.exe',
    'C:\Program Files\Python312\python.exe',
    'C:\Program Files\Python311\python.exe'
  ) | Where-Object { $_ }

  foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) {
      continue
    }

    if ($candidate -like '*WindowsApps*') {
      continue
    }

    return $candidate
  }

  return $null
}

if (-not (Test-Path -LiteralPath $venvPython)) {
  Write-Host "Creating local virtual environment..." -ForegroundColor Cyan
  $systemPython = Find-SystemPython
  if (-not $systemPython) {
    throw "Unable to find a real Python installation. Install Python 3.11+ or set PYTHON_EXE to your python.exe path."
  }

  if ($systemPython -like '*\py.exe') {
    & $systemPython -3 -m venv (Join-Path $root '.venv')
  }
  else {
    & $systemPython -m venv (Join-Path $root '.venv')
  }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
  throw "Unable to create .venv. Install Python 3.11+ or set PYTHON_EXE to your python.exe path."
}

Write-Host "Installing backend dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
& $venvPython -m pip install -r (Join-Path $root 'platform\api\requirements.txt')
& $venvPython -m pip install -r (Join-Path $root 'platform\browser-stream\requirements.txt')

$externalRepo = Join-Path $root 'external\ai-agent-browser'
if (Test-Path -LiteralPath (Join-Path $externalRepo 'requirements.txt')) {
  Write-Host "Installing external agent dependencies..." -ForegroundColor Cyan
  & $venvPython -m pip install -r (Join-Path $externalRepo 'requirements.txt')
}

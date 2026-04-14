$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$webDir = Join-Path $root 'web'

Set-Location -LiteralPath $webDir
npm run dev

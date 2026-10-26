$ErrorActionPreference = 'Stop'
$bootstrapScript = Join-Path $PSScriptRoot 'scripts/bootstrap.py'
$projectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$toolPython = Join-Path $PSScriptRoot '.tools/bootstrap/Scripts/python.exe'
$selectedPython = $null
$prefixArguments = @()
if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
    $selectedPython = $projectPython
} elseif (Test-Path -LiteralPath $toolPython -PathType Leaf) {
    $selectedPython = $toolPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $selectedPython = (Get-Command py).Source
    $prefixArguments = @('-3.12')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $selectedPython = (Get-Command python).Source
}
if (-not $selectedPython) {
    Write-Error 'Python 3.12 is required. Install the native release from https://www.python.org/downloads/ .'
    exit 2
}
$pauseArguments = @()
if ($args.Count -eq 0 -and -not [Console]::IsInputRedirected) {
    $pauseArguments = @('--pause')
}
& $selectedPython @prefixArguments $bootstrapScript @pauseArguments @args
exit $LASTEXITCODE

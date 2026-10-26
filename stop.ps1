$ErrorActionPreference = 'Stop'
$startScript = Join-Path $PSScriptRoot 'start.ps1'
$pauseArguments = @()
if ($args.Count -eq 0 -and -not [Console]::IsInputRedirected) {
    $pauseArguments = @('--pause')
}
& $startScript stop @pauseArguments @args
exit $LASTEXITCODE

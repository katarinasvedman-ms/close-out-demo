[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw 'OutputDirectory must be new; existing packages are never overwritten.'
}
New-Item -ItemType Directory -Path $output | Out-Null
$adapter = New-Item -ItemType Directory -Path (Join-Path $output 'adapter')
$workflows = New-Item -ItemType Directory -Path (Join-Path $output 'workflows')

function Copy-SourceTree([string] $Source, [string] $Destination) {
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File |
        Where-Object { $_.Extension -ne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
        ForEach-Object {
            $relative = [IO.Path]::GetRelativePath($sourceRoot, $_.FullName)
            $target = Join-Path $Destination $relative
            New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target
        }
}

Push-Location $root
try {
    Copy-SourceTree 'src\agents' (Join-Path $adapter 'agents')
    Copy-SourceTree 'src\shared' (Join-Path $adapter 'shared')
    Copy-SourceTree 'contracts' (Join-Path $adapter 'contracts')
    Copy-Item -LiteralPath 'src\flow_functions\function_app.py', 'src\flow_functions\host.json' -Destination $adapter
    Copy-SourceTree 'src\logicapps' $workflows
    & uv export --frozen --extra agents --extra flow --no-dev --no-emit-project --no-hashes --no-header --format requirements-txt --output-file (Join-Path $adapter 'requirements.txt') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency export failed.' }
    Compress-Archive -Path (Join-Path $adapter '*') -DestinationPath (Join-Path $output 'adapter.zip')
    Compress-Archive -Path (Join-Path $workflows '*') -DestinationPath (Join-Path $output 'workflows.zip')
    Write-Output "Created adapter.zip (requires Linux Python 3.11 remote dependency build) and workflows.zip in $output. Nothing deployed."
}
finally {
    Pop-Location
}

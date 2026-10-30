[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z][a-z0-9-]{1,23}$')]
    [string] $Environment,
    [switch] $NewAssessment
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

function Require-Tool([string] $Name, [string] $Instructions) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Host "`nMissing prerequisite: $Name"
        Write-Host $Instructions
        Write-Host "Then reopen PowerShell and rerun: .\scripts\deploy-demo.ps1 -Environment $Environment"
        exit 2
    }
}

# Keep this launcher compatible with Windows PowerShell for first-time operators.
if ($PSVersionTable.PSVersion.Major -lt 7) {
    Require-Tool 'pwsh' 'Install PowerShell 7: https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows'
    $forward = @('-NoProfile', '-File', $PSCommandPath, '-Environment', $Environment)
    if ($NewAssessment) { $forward += '-NewAssessment' }
    & pwsh @forward
    exit $LASTEXITCODE
}

$PSNativeCommandUseErrorActionPreference = $false
Require-Tool 'az' 'Install Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli-windows'
Require-Tool 'uv' 'Install uv: https://docs.astral.sh/uv/getting-started/installation/'

Push-Location $root
try {
    Write-Host 'Close-out demo guided setup. Fabric items will not be provisioned.'
    & az account show --query id --output tsv --only-show-errors 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Sign in with the account that can access your Azure subscription and Fabric workspace.'
        & az login --output none
        if ($LASTEXITCODE -ne 0) { throw 'Sign-in did not finish. Rerun when you can sign in.' }
    }

    Write-Host 'Preparing locked Python dependencies (uv obtains Python 3.11 if needed)...'
    & uv sync --frozen --extra agents --extra flow --python 3.11
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check network/package access and rerun.' }

    $installerArgs = @('--environment', $Environment)
    if ($NewAssessment) { $installerArgs += '--new-assessment' }
    & uv run --frozen --extra agents --extra flow python -m agents.installer @installerArgs
    $result = $LASTEXITCODE
}
catch {
    Write-Host ("Setup paused: " + $_.Exception.Message)
    $result = 2
}
finally {
    Pop-Location
}
exit $result

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [guid] $WorkspaceId,
    [Parameter(Mandatory = $true)]
    [string] $OutputPath,
    [guid] $ConnectionId = [guid]::Empty,
    [guid] $ExpectedPrincipalId = [guid]::Empty,
    [string] $WorkflowResourceId = '',
    [string] $ExpectedAudience = ''
)

$ErrorActionPreference = 'Stop'
function Read-Fabric([string] $Url) {
    $body = & az rest --method get --url $Url --resource https://api.fabric.microsoft.com --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw 'Fabric read-back failed; no configuration changed.' }
    return ($body | ConvertFrom-Json)
}

$base = 'https://api.fabric.microsoft.com/v1'
if ($ConnectionId -ne [guid]::Empty) {
    if ($ExpectedPrincipalId -eq [guid]::Empty -or
        $WorkflowResourceId -notmatch '^/subscriptions/[a-fA-F0-9-]+/resourceGroups/[^/]+/providers/Microsoft.Web/sites/[^/]+$' -or
        -not $ExpectedAudience) {
        throw 'Credential verification requires the exact principal, workflow resource ID and audience.'
    }
    $connection = Read-Fabric "$base/connections/$ConnectionId"
    $body = & az rest --method get --url "https://management.azure.com$WorkflowResourceId/config/authsettingsV2?api-version=2023-12-01" --resource https://management.azure.com --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw 'Workflow authentication read-back failed.' }
    $auth = ($body | ConvertFrom-Json).properties
    $aad = $auth.identityProviders.azureActiveDirectory
    $principals = @($aad.validation.defaultAuthorizationPolicy.allowedPrincipals.identities)
    $expectedPath = 'https://' + ($WorkflowResourceId -split '/')[-1] + '.azurewebsites.net;' + $ExpectedAudience
    $ready = $connection.connectionDetails.type -eq 'WebForPipeline' -and
        $connection.connectionDetails.path -eq $expectedPath -and
        $connection.credentialDetails.credentialType -eq 'ServicePrincipal' -and
        -not $connection.credentialDetails.skipTestConnection -and
        $auth.platform.enabled -and $aad.enabled -and $auth.globalValidation.requireAuthentication -and
        $auth.globalValidation.unauthenticatedClientAction -eq 'Return401' -and
        $aad.validation.allowedAudiences -contains $ExpectedAudience -and
        $principals.Count -eq 1 -and $principals[0] -eq $ExpectedPrincipalId.ToString()
    $report = [ordered]@{
        checkedAt = [datetime]::UtcNow.ToString('o')
        workspaceId = $WorkspaceId.ToString()
        connectionId = $ConnectionId.ToString()
        authentication = 'ServicePrincipal'
        expectedPrincipalId = $ExpectedPrincipalId.ToString()
        status = $(if ($ready) { 'CallbackCredentialPrerequisitesPresent' } else { 'Blocked' })
        nativeFoundryIq = 'NotTestedByThisCheck'
        workflowDispatch = 'NotTestedByThisCheck'
        changesMade = $false
    }
    $json = $report | ConvertTo-Json -Depth 20
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath), $json)
    Write-Output $json
    if (-not $ready) { exit 2 }
    exit 0
}
$workspace = Read-Fabric "$base/workspaces/$WorkspaceId"
$types = @()
$continuation = $null
$seen = @{}
for ($page = 0; $page -lt 100; $page++) {
    $url = "$base/connections/supportedConnectionTypes?showAllCreationMethods=true"
    if ($continuation) { $url += '&continuationToken=' + [uri]::EscapeDataString($continuation) }
    $response = Read-Fabric $url
    $types += @($response.value | Where-Object type -In @('WebForPipeline', 'RestService'))
    $continuation = $response.continuationToken
    if (-not $continuation) { break }
    if ($seen.ContainsKey($continuation)) { throw 'Repeated connection-type continuation token.' }
    $seen[$continuation] = $true
}
if ($continuation) { throw 'Connection-type inventory exceeded the bounded page limit.' }
$web = @($types | Where-Object type -eq 'WebForPipeline')
$supportsIdentity = $web.Count -eq 1 -and $web[0].supportedCredentialTypes -contains 'WorkspaceIdentity'
$identity = $workspace.workspaceIdentity
$ready = $supportsIdentity -and [bool]$identity.servicePrincipalId
$report = [ordered]@{
    checkedAt = [datetime]::UtcNow.ToString('o')
    workspaceId = $WorkspaceId.ToString()
    workspaceIdentity = $identity
    connections = $types
    webWorkspaceIdentitySupported = $supportsIdentity
    status = $(if ($ready) { 'DispatchIdentityPrerequisitesPresent' } else { 'Blocked' })
    nativeFoundryIq = 'NotTestedByThisCheck'
    changesMade = $false
}
$json = $report | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath), $json)
Write-Output $json
if (-not $ready) { exit 2 }

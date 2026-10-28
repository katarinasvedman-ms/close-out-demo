# Environment-specific setup

## Synthetic document Storage security-control exception

The development environment's external security controls disabled public network
access on the newly created knowledge Storage account. Upload returned
`AuthorizationFailure` despite the correct container-scoped Blob Data Contributor
role. The account had `publicNetworkAccess=Disabled`. The user explicitly
authorized the `SecurityControl=Ignore` resource-group exception if this occurred.

Apply only to the dedicated knowledge resource group, preserving other tags:

```powershell
az group update --name "<knowledge resource group>" --set tags.SecurityControl=Ignore
```

Re-run the knowledge Bicep deployment from `FOUNDRY-IQ.md` with the additional
parameter `securityControlIgnore=true`. This preserves the exception on later
deployments and restores the explicitly declared authenticated public endpoint.
Blob containers remain private; shared keys and anonymous access remain disabled.
Do not apply this exception to unrelated groups or infer approval for production.

Verify network state and run the actual upload/index verification script:

```powershell
az storage account show --ids "<storage ARM ID>" --query "{network:publicNetworkAccess,anonymous:allowBlobPublicAccess,sharedKeys:allowSharedKeyAccess}"
uv run --frozen --extra agents python -m agents.knowledge_setup --infra .local\foundry\knowledge-infra.json --site SE-STO-0042 --site SE-GOT-0017
```

Expected state is `Enabled`, `false`, `false`, followed by successful authenticated
uploads and verified canonical artifact/site/version text in the shared native
Blob source's generated index.

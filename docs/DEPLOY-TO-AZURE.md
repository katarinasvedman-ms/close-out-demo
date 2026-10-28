# Fresh headless deployment

The README **Deploy to Azure** button opens a resource-group-scoped ARM template.
Choose your subscription and a new, dedicated resource group, and enter a globally
unique Foundry resource name. No existing Foundry account/project is required.
Other fields have defaults. Resource-group location does not relocate the services:
Foundry, its regional Standard GPT-4o deployment, Blob Storage and
monitoring use Sweden Central; Basic AI Search uses France Central.

The template creates the account/project, model, managed identities, scoped RBAC,
private documents container, Search, project connections, Log Analytics and
Application Insights. All resources are in the selected group. There is no hosted
agent, application container, container registry or capability host.
Public endpoints require authentication; Blob
anonymous access, shared keys and Foundry/Search local authentication are disabled.
Documents are shared synthetic operator knowledge, not per-user ACL-protected data.

This deploys the current headless increment, **not** the complete design-document
architecture. Fabric, Power Platform, upload APIs, workflows, governed actions,
Content Understanding, packages and Presenter hosting are not included.

## Prerequisites and portal deployment

Use an interactive target-tenant user with resource-creation
and role-assignment permissions in the group (for example Owner). The template
uses the deployer's object ID; no tenant, subscription or principal IDs are copied
from the development environment. Automation must set `operatorPrincipalType` to
`ServicePrincipal` and authenticate consistently for the completion phase.

The subscription needs the relevant providers registered:
`Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.Storage`,
`Microsoft.OperationalInsights` and
`Microsoft.Insights`. It also needs regional model quota (default capacity 30),
model/version availability, France Central Search capacity, and permission to use
the current preview IQ/agent APIs. Deployment can fail on quota, policy, naming or
feature availability; it does not switch regions, models or deployment SKUs.
Basic Search, model inference and monitoring incur charges.

In the portal, review the template and parameters before deploying. Record the
successful deployment's **name** from the resource group's Deployments blade.
ARM success alone does not upload documents, create an IQ knowledge base or
publish agents.

The button uses public `main/infra/fresh/azuredeploy.json`. That file is generated,
not manually maintained. A local badge cannot deploy unpublished changes:
the Bicep, generated JSON and application code must be committed and pushed
together before sharing the button. Publishing is a separate repository action.

## Complete and verify the demo

Clone the same published revision and run from its root. Prerequisites are Python
3.11/uv and Azure CLI. Authenticate Azure CLI to the intended tenant/subscription
yourself; the completion command never starts login or changes Azure CLI's selected
subscription. No azd environment or container tooling is needed.

```powershell
uv sync --frozen --extra dev --extra agents --python 3.11
az account show --query "{subscription:id,tenant:tenantId,user:user.name}"
# Substitute the selected group and the successful portal deployment name:
uv run --frozen --extra agents python -m agents.bootstrap --resource-group "<resource-group>" --deployment "<deployment-name>"
```

The command reads and checks the deployment outputs, generates synthetic assets
in an isolated local target directory, uploads the eight
canonical text counterparts, creates the shared native Blob IQ source/base with
Low retrieval reasoning and registers the Foundry prompt agents.
No application code or hosted runtime is deployed.

It then invokes both cases through the **local** Agent Framework test client.
Model inference and IQ retrieval are live in Foundry; the facts function runs
locally against the scoped synthetic MCP stub. The main case must return exactly
the three cited gaps; the Ready fallback must have no gaps/rework.
Target-specific configuration and verified receipts stay in ignored
`.local\fresh\<subscription-id>\<foundry-name>\`; `completion.json` is written only
after both checks pass. Existing development configuration is not overwritten;
azd environments are not created or modified.

For infrastructure updates redeploy this same resource-group template; for
prompt/IQ updates rerun the completion command. Unchanged prompt definitions are
reused. Stop on errors, allow for role propagation, resolve any reported policy or
quota issue, then retry. Completed phases may have incurred charges.
Rerunning uses the same target and verifies it again.

The coordinator playground cannot execute a function declaration backed by a
local stub. A real authenticated Fabric Data Agent MCP binding is still needed
for structured facts without a local client. This missing integration is not
replaced by a hosted agent or presented as complete.

If external security controls disable Storage networking, obtain the target
owner's explicit approval before applying any `SecurityControl=Ignore` exception.
The portal template/completion command do not change resource-group tags or bypass
policy. See `MANUAL_STEPS.md` for the diagnostic pattern; the development exception
does not authorize an exception in another subscription. Restore the declared
Storage network state through the target's approved process, then retry.

## Template maintenance and verification limits

The checked-in template uses Bicep **0.39.26**, also pinned in CI:

```powershell
az bicep build --file infra\fresh\main.bicep --outfile infra\fresh\azuredeploy.json
uv run --frozen --extra dev pytest tests\unit\test_bootstrap.py
```

CI recompiles it and checks freshness.
The portal template passed non-mutating ARM validation in the current subscription;
that did not provision another environment.
Compilation/offline completion tests do not prove a fresh subscription has quota,
feature access, usable model capacity or the required tenant permissions.
`TODO(verify)`: run the portal flow and completion command in an explicitly chosen
fresh target, and repeat infrastructure deployment to establish fresh-cloud
idempotence. Existing shared IQ/agent live proofs are not a fresh-deployment proof.
No new target environment is created merely by adding these files.

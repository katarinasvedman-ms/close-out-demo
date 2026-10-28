# Implementation gates

## Current progress

The M0 development harness, provisional contracts, deterministic seed and local integration stubs are implemented. The user authorized proceeding without waiting for cross-track sign-off and may not need full end-to-end integration. `uv run --frozen python -m shared.local_demo` generates synthetic evidence and replays readiness transitions locally. It makes no cloud calls, executes no business actions and never updates Dataverse.

The user subsequently supplied an existing Foundry project and authorized necessary model deployment. A regional GPT-4o `2024-11-20` Standard deployment (capacity 30, Sweden Central) and two active prompt agents exist. Agent Framework invokes them with real image/PDF input and scoped synthetic facts. Synthetic live smoke covers rejected originals, accepted replacements, cited blocked-case rework and an empty-gap Ready fallback. This does not complete upload APIs, Content Understanding, package generation or live cross-track integration.

The GitHub repository is `https://github.com/katarinasvedman-ms/close-out-demo.git`. Owner approval, the complete cloud foundation, governed actions and live integrations remain outstanding. Synthetic agent inference is not full end-to-end acceptance.

The solution uses Foundry prompt agents and native IQ, not a hosted agent.
The local Agent Framework client executes the synthetic-facts function during
verification. A native authenticated Fabric Data Agent MCP integration is still
required before the full coordinator can run without that local test client.
Experimental hosted-agent resources created earlier are outside the current
solution and have not been deleted by this repository cleanup.

The user also authorized Blob-backed Foundry IQ: France Central Basic Search
now uses one native `azureBlob` knowledge source and one shared knowledge base
over private Sweden Central Blob Storage. IQ manages the generated ingestion
pipeline and indexes four synthetic text counterparts per selected site.
Both sites use coordinator version 9 and one exact MCP connection authenticated
with project managed identity. A new operator note was uploaded and retrieved
after managed synchronization without editing the generated Search objects.
The old per-site bases/sources, manual pipelines and connections were retired.
This shared synthetic operator demo does not enforce per-user document ACLs.
Retrieval now uses Low LLM query planning with the existing Sweden Central
GPT-4o deployment, explicit site/version retrieval instructions and extractive
output. Search's managed identity receives account-scoped Cognitive Services
User through Bicep. No additional model or region is introduced.
See [FOUNDRY-IQ.md](FOUNDRY-IQ.md) and the explicitly approved development Storage
exception in [MANUAL_STEPS.md](MANUAL_STEPS.md). Fabric facts remain a stub.

## Required external inputs

| Gate | Required input or evidence | Affected work |
|---|---|---|
| Repository setup | Git/origin are connected; supply remaining track-owner GitHub handles and configure protected environments and all-three-owner contract sign-off | Publishing/running hosted CI and enforcing reviews |
| Contract review | Review `contracts/REVIEW-DECISIONS.md`; agree the drafted schemas, nullability, readinessMissing, physical mapping and result storage | Approved release and affected live integrations, not provisional seed/stub development |
| Repeated actions | Agree how caseId + transition distinguishes repeated rework after customer return and multiple actions during a transition | Idempotency receipts and recovery |
| Cloud approval | Existing Foundry project/model, scoped Blob/IQ infrastructure and documented dedicated-group Storage exception authorized. The fresh portal template is available locally; target provisioning requires selected names, quota, permissions and policy approval. Hosted-agent deployment is excluded. Search is France Central; other components remain Sweden Central | Remaining broad provisioning and application deployment |
| Power Platform | Published Dataverse mappings, application-user permissions, actual service-endpoint message, approval evidence and authorized closure trigger | Real dev Dataverse integration |
| Fabric | Eventstream endpoint/auth, Data Agent MCP transport/auth, readiness contract and workspace identity | Live event, readiness and retrieval paths |
| Package generation | Agreed DOCX/PDF contract and supported conversion mechanism with residency/cost approval | Close-out package generation |
| Governance | Verified Prompt Shields/guardrail automation and Purview audit permissions/coverage | Live governance demonstrations |

Preserve `TODO(verify)` for unsettled platform APIs and availability. No external-owner approval is inferred from local tests. No cloud resources or GitHub repository are created automatically to bypass these gates.

## Approved regional exception

The user confirmed that Sweden Central has no AI Search capacity and approved **France Central (`francecentral`) for Azure AI Search temporarily**. Basic Search capacity, cross-region Blob text indexing and native IQ retrieval are now verified for the selected synthetic sites, not every Search capability.

Keep the main deployment location `swedencentral`; `infra/knowledge/main.bicep` independently parameterizes `searchLocation`. Model, existing-project knowledge and fresh-project portal Bicep entry points exist, not the full infrastructure foundation. Blob, Foundry, model deployments and the other components are not relocated by this exception.

`TODO(verify)`: production identity/network policies, enrichment/model co-location requirements and live event-to-record latency. This increment has no live Dataverse record path, so it cannot establish that latency gate.

## Platform documentation checks

The following official references were checked during foundation implementation. Documentation availability is not proof of subscription capacity or permission to deploy.

- [Content Understanding region support](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/language-region-support) lists Sweden Central. It distinguishes storage location from processing location; verify processing configuration against the Sweden Central-only requirement before using a global or data-zone deployment.
- [Azure AI Search regional support](https://learn.microsoft.com/en-us/azure/search/search-region-support#europe) lists Sweden Central with agentic retrieval, but also marks it as capacity-constrained for new search services. The user's approved resolution is temporary France Central placement for Search only; the selected Basic/Blob/IQ path is now verified there.

Sweden Central Standard GPT-4o `2024-11-20` capacity/quota, the supplied project's authenticated SDK access and actual model/agent invocation have now been verified. What-if showed only the new model deployment changing; unrelated resources were ignored. The model uses `Microsoft.DefaultV2` filtering and no automatic version upgrade. That model deployment did not change account network settings or roles.

`TODO(verify)`: remaining component GA/preview labels, structured MCP URL support, production connector authorization, APIM MCP exposure and guardrail automation remain unsettled. Native IQ retrieval uses the documented preview MCP API with managed identity; indexing is of synthetic text counterparts, not OCR. The native GPT-4o vision/PDF path is not Content Understanding. Broad Foundry evaluation-suite generation is deferred; the repeatable credentialed synthetic smoke command is not a production evaluation gate.

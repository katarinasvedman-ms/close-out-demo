# Implementation gates

## Current progress

The M0 development harness, provisional contracts, deterministic seed and local integration stubs are implemented. The user authorized proceeding without waiting for cross-track sign-off and may not need full end-to-end integration. `uv run --frozen python -m shared.local_demo` generates synthetic evidence and replays readiness transitions locally. It makes no cloud calls, executes no business actions and never updates Dataverse.

The user subsequently supplied an existing Foundry project and authorized necessary model deployment. A regional GPT-4o `2024-11-20` Standard deployment (capacity 30, Sweden Central) and two active prompt agents now exist. Agent Framework invokes them with real image/PDF input and scoped local MCP/document tools. Synthetic live smoke covers rejected originals, accepted replacements, cited blocked-case rework and an empty-gap Ready fallback. This does not complete upload, Content Understanding, Foundry IQ, package generation or live cross-track integration.

The GitHub repository is `https://github.com/katarinasvedman-ms/close-out-demo.git`. Owner approval, the complete cloud foundation, governed actions and live integrations remain outstanding. Synthetic agent inference is not full end-to-end acceptance.

## Required external inputs

| Gate | Required input or evidence | Affected work |
|---|---|---|
| Repository setup | Git/origin are connected; supply remaining track-owner GitHub handles and configure protected environments and all-three-owner contract sign-off | Publishing/running hosted CI and enforcing reviews |
| Contract review | Review `contracts/REVIEW-DECISIONS.md`; agree the drafted schemas, nullability, readinessMissing, physical mapping and result storage | Approved release and affected live integrations, not provisional seed/stub development |
| Repeated actions | Agree how caseId + transition distinguishes repeated rework after customer return and multiple actions during a transition | Idempotency receipts and recovery |
| Cloud approval | Existing Foundry project and needed model deployments authorized; regional model/prompt agents are active. Other infrastructure, identities, hosting, network exposure and budgets still require approval. Search region is France Central; other components remain Sweden Central | Remaining provisioning and hosted application deployment |
| Power Platform | Published Dataverse mappings, application-user permissions, actual service-endpoint message, approval evidence and authorized closure trigger | Real dev Dataverse integration |
| Fabric | Eventstream endpoint/auth, Data Agent MCP transport/auth, readiness contract and workspace identity | Live event, readiness and retrieval paths |
| Package generation | Agreed DOCX/PDF contract and supported conversion mechanism with residency/cost approval | Close-out package generation |
| Governance | Verified Prompt Shields/guardrail automation and Purview audit permissions/coverage | Live governance demonstrations |

Preserve `TODO(verify)` for unsettled platform APIs and availability. No external-owner approval is inferred from local tests. No cloud resources or GitHub repository are created automatically to bypass these gates.

## Approved regional exception

The user confirmed that Sweden Central has no AI Search capacity and approved **France Central (`francecentral`) for Azure AI Search temporarily**. This resolves the Search region decision, not the remaining provisioning approvals or capacity verification in France Central.

Keep the main deployment location `swedencentral` and plan an independent Search location parameter (`searchLocation`, supplied by `AZURE_SEARCH_LOCATION`). Only the existing-account regional model Bicep template exists; there is no full infrastructure/azd configuration yet. Blob, Foundry, model deployments and the other components are not relocated by this exception.

`TODO(verify)`: confirm France Central Search SKU/feature capacity and cross-region Blob indexing, retrieval, network/identity access and latency. Check any enrichment/model co-location requirements without broadening the regional exception automatically.

## Platform documentation checks

The following official references were checked during foundation implementation. Documentation availability is not proof of subscription capacity or permission to deploy.

- [Content Understanding region support](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/language-region-support) lists Sweden Central. It distinguishes storage location from processing location; verify processing configuration against the Sweden Central-only requirement before using a global or data-zone deployment.
- [Foundry hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents#limits-pricing-and-availability) lists Sweden Central and describes separate project and runtime agent identities. Hosting selection, resource permissions, quota and an authorized deployment probe are still required.
- [Azure AI Search regional support](https://learn.microsoft.com/en-us/azure/search/search-region-support#europe) lists Sweden Central with agentic retrieval, but also marks it as capacity-constrained for new search services. The user's approved resolution is temporary France Central placement for Search only; deployment capacity and cross-region compatibility still require verification.

Sweden Central Standard GPT-4o `2024-11-20` capacity/quota, the supplied project's authenticated SDK access and actual model/agent invocation have now been verified. What-if showed only the new model deployment changing; unrelated resources were ignored. The model uses `Microsoft.DefaultV2` filtering and no automatic version upgrade. No account network settings or role assignments were changed.

`TODO(verify)`: remaining component GA/preview labels, Foundry IQ API/SDK selection, connector authentication, APIM MCP exposure and guardrail automation remain unsettled. The current native GPT-4o vision/PDF implementation is not a claim that Content Understanding has been integrated. Broad Foundry evaluation-suite generation is deferred; the repeatable credentialed synthetic smoke command is not a production evaluation gate.

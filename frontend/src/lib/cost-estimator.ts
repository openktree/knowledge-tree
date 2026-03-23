/**
 * Cost estimation utility for Knowledge Tree queries.
 *
 * Estimates the USD cost of running a query based on the agent architecture:
 * orchestrator → sub-explorers, plus the 4-phase build pipeline
 * (decomposition, dimension generation, edge resolution) and synthesis.
 *
 * Pricing is loaded from a JSON config file with full OpenRouter model IDs.
 */

import type {
  CostEstimate,
  CostBreakdownCategory,
  ModelConfig,
  ModelRoles,
} from "@/types";
import pricingData from "@/config/model-pricing.json";

// ---------------------------------------------------------------------------
// Model pricing lookup (per 1 million tokens)
// ---------------------------------------------------------------------------

interface ModelPricing {
  inputPerMillion: number;
  outputPerMillion: number;
}

const MODEL_PRICING: Record<string, ModelPricing> = pricingData.models;
const DEFAULT_PRICING: ModelPricing = pricingData.default;

// ---------------------------------------------------------------------------
// Constants — token estimates per call type
// ---------------------------------------------------------------------------

/** Estimated cost per Brave Search API call (USD). */
const BRAVE_SEARCH_COST = 0.005;

/** Per-category token estimates: [input_tokens, output_tokens] per call. */
const TOKEN_ESTIMATES = {
  orchestrator: { input: 4000, output: 800 },
  sub_explorer: { input: 3000, output: 600 },
  decomposition: { input: 2000, output: 4000 },
  dimension: { input: 3000, output: 1500 },
  edge_resolution: { input: 2500, output: 1000 },
  synthesis: { input: 5000, output: 2000 },
} as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getPricing(modelId: string): ModelPricing {
  return MODEL_PRICING[modelId] ?? DEFAULT_PRICING;
}

function isSupported(modelId: string): boolean {
  return modelId in MODEL_PRICING;
}

/**
 * Calculate the cost of a single model call given an approximate input and
 * output token count.
 */
function callCost(
  pricing: ModelPricing,
  inputTokens: number,
  outputTokens: number,
): number {
  return (
    (inputTokens / 1_000_000) * pricing.inputPerMillion +
    (outputTokens / 1_000_000) * pricing.outputPerMillion
  );
}

function makeCategory(
  label: string,
  modelId: string,
  calls: number,
  inputPerCall: number,
  outputPerCall: number,
): CostBreakdownCategory {
  const pricing = getPricing(modelId);
  const totalInput = calls * inputPerCall;
  const totalOutput = calls * outputPerCall;
  return {
    label,
    model_id: modelId,
    calls,
    input_tokens: totalInput,
    output_tokens: totalOutput,
    cost: callCost(pricing, totalInput, totalOutput),
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Estimate the total USD cost of a Knowledge Tree query.
 *
 * Cost model reflects the 4-phase build pipeline:
 *
 * | Category        | Model Role      | Calls                              |
 * |-----------------|-----------------|-------------------------------------|
 * | Orchestrator    | orchestrator    | 10 turns (5 if nav-only)           |
 * | Sub-explorers   | sub_explorer    | max(1,E/4) subs * 7 turns each    |
 * | Decomposition   | decomposition   | E calls (Phase 1 external search)  |
 * | Dimension gen   | dimension       | E calls (Phase 3, 1 model/node)    |
 * | Edge resolution | edge_resolution | E calls (Phase 4, 1 LLM/node)     |
 * | Synthesis       | synthesis       | 4 turns                            |
 * | Brave Search    | N/A             | E calls * $0.005                   |
 */
export function estimateCost(params: {
  navBudget: number;
  exploreBudget: number;
  models: ModelConfig[];
  modelRoles?: ModelRoles;
}): CostEstimate {
  const { navBudget, exploreBudget, models, modelRoles } = params;
  const E = exploreBudget;

  // Resolve model IDs per role. When modelRoles is provided, use it.
  // Otherwise fall back to the first model in the list (or empty string for
  // default pricing).
  const fallbackModel = models.length > 0 ? models[0].model_id : "";
  const roleModel = (role: keyof ModelRoles): string =>
    modelRoles ? modelRoles[role] : fallbackModel;

  // Track unsupported models (not in pricing JSON)
  const unsupportedSet = new Set<string>();
  const checkSupport = (modelId: string) => {
    if (modelId && !isSupported(modelId)) {
      unsupportedSet.add(modelId);
    }
  };

  // Build categories
  const categories: CostBreakdownCategory[] = [];

  // 1. Orchestrator: 10 turns normally, 5 if nav-only (E=0)
  const orchestratorModel = roleModel("orchestrator");
  checkSupport(orchestratorModel);
  const orchestratorCalls = E > 0 ? 10 : 5;
  categories.push(
    makeCategory(
      "Orchestrator",
      orchestratorModel,
      orchestratorCalls,
      TOKEN_ESTIMATES.orchestrator.input,
      TOKEN_ESTIMATES.orchestrator.output,
    ),
  );

  // 2. Sub-explorers: max(1, E/4) sub-explorers * 7 turns each (only if E > 0)
  const subExplorerModel = roleModel("sub_explorer");
  checkSupport(subExplorerModel);
  const numSubExplorers = E > 0 ? Math.max(1, Math.ceil(E / 4)) : 0;
  const subExplorerCalls = numSubExplorers * 7;
  if (subExplorerCalls > 0) {
    categories.push(
      makeCategory(
        "Sub-explorers",
        subExplorerModel,
        subExplorerCalls,
        TOKEN_ESTIMATES.sub_explorer.input,
        TOKEN_ESTIMATES.sub_explorer.output,
      ),
    );
  }

  // 3. Decomposition: E calls — Phase 1 external search + fact decomposition
  const decompositionModel = roleModel("decomposition");
  checkSupport(decompositionModel);
  if (E > 0) {
    categories.push(
      makeCategory(
        "Decomposition",
        decompositionModel,
        E,
        TOKEN_ESTIMATES.decomposition.input,
        TOKEN_ESTIMATES.decomposition.output,
      ),
    );
  }

  // 4. Dimension generation: E calls — Phase 3, one model per node
  const dimensionModel = roleModel("dimension");
  checkSupport(dimensionModel);
  if (E > 0) {
    categories.push(
      makeCategory(
        "Dimension gen",
        dimensionModel,
        E,
        TOKEN_ESTIMATES.dimension.input,
        TOKEN_ESTIMATES.dimension.output,
      ),
    );
  }

  // 5. Edge resolution: E calls — Phase 4, fact-grounded LLM classification
  const edgeResolutionModel = roleModel("edge_resolution");
  checkSupport(edgeResolutionModel);
  if (E > 0) {
    categories.push(
      makeCategory(
        "Edge resolution",
        edgeResolutionModel,
        E,
        TOKEN_ESTIMATES.edge_resolution.input,
        TOKEN_ESTIMATES.edge_resolution.output,
      ),
    );
  }

  // 6. Synthesis: 4 turns
  const synthesisModel = roleModel("synthesis");
  checkSupport(synthesisModel);
  categories.push(
    makeCategory(
      "Synthesis",
      synthesisModel,
      4,
      TOKEN_ESTIMATES.synthesis.input,
      TOKEN_ESTIMATES.synthesis.output,
    ),
  );

  // 7. Brave Search cost (not a model call)
  const searchApiCost = E * BRAVE_SEARCH_COST;

  // Totals
  const modelCost = categories.reduce((sum, c) => sum + c.cost, 0);
  const totalCost = modelCost + searchApiCost;
  const totalModelCalls = categories.reduce((sum, c) => sum + c.calls, 0);

  return {
    estimated_cost_usd: totalCost,
    breakdown: {
      nav_reads: navBudget,
      explore_creates: E,
      model_calls: totalModelCalls,
      search_api_calls: E,
    },
    models: models.map((m) => m.model_id),
    categories,
    unsupported_models: [...unsupportedSet],
  };
}

/**
 * Format a USD cost for display.  Returns "< $0.001" for amounts below one
 * tenth of a cent, otherwise "$X.XXX".
 */
export function formatCost(usd: number): string {
  if (usd < 0.001) {
    return "< $0.001";
  }
  return `$${usd.toFixed(3)}`;
}

import { describe, it, expect } from "vitest";
import { estimateCost, formatCost } from "@/lib/cost-estimator";
import type { ModelConfig, ModelRoles } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeModel(model_id: string): ModelConfig {
  return { model_id, provider: "openrouter", display_name: model_id };
}

const DEFAULT_MODELS: ModelConfig[] = [
  makeModel("openrouter/x-ai/grok-4.1-fast"),
  makeModel("openrouter/anthropic/claude-3.5-sonnet"),
];

const SAMPLE_ROLES: ModelRoles = {
  orchestrator: "openrouter/x-ai/grok-4.1-fast",
  sub_explorer: "openrouter/x-ai/grok-4.1-fast",
  edge_resolution: "openrouter/x-ai/grok-4.1-fast",
  decomposition: "openrouter/google/gemini-2.0-flash-001",
  dimension: "openrouter/google/gemini-2.0-flash-001",
  synthesis: "openrouter/x-ai/grok-4.1-fast",
};

// ---------------------------------------------------------------------------
// estimateCost — default budgets
// ---------------------------------------------------------------------------

describe("estimateCost", () => {
  it("returns reasonable values with default budgets", () => {
    const result = estimateCost({
      navBudget: 200,
      exploreBudget: 20,
      models: DEFAULT_MODELS,
    });

    expect(result.estimated_cost_usd).toBeGreaterThan(0);
    expect(result.breakdown.nav_reads).toBe(200);
    expect(result.breakdown.explore_creates).toBe(20);
    expect(result.breakdown.search_api_calls).toBe(20);
    expect(result.breakdown.model_calls).toBeGreaterThan(0);
    expect(result.models).toEqual([
      "openrouter/x-ai/grok-4.1-fast",
      "openrouter/anthropic/claude-3.5-sonnet",
    ]);
    // Should have categories
    expect(result.categories.length).toBeGreaterThan(0);
  });

  it("includes all expected categories with explore budget > 0", () => {
    const result = estimateCost({
      navBudget: 100,
      exploreBudget: 8,
      models: DEFAULT_MODELS,
    });

    const labels = result.categories.map((c) => c.label);
    expect(labels).toContain("Orchestrator");
    expect(labels).toContain("Sub-explorers");
    expect(labels).toContain("Decomposition");
    expect(labels).toContain("Dimension gen");
    expect(labels).toContain("Edge resolution");
    expect(labels).toContain("Synthesis");
  });

  // ---------------------------------------------------------------------------
  // Zero budgets (nav-only query)
  // ---------------------------------------------------------------------------

  it("returns minimal cost with zero explore budget", () => {
    const result = estimateCost({
      navBudget: 50,
      exploreBudget: 0,
      models: DEFAULT_MODELS,
    });

    // Should only have orchestrator (5 turns) and synthesis (4 turns)
    const labels = result.categories.map((c) => c.label);
    expect(labels).toContain("Orchestrator");
    expect(labels).toContain("Synthesis");
    expect(labels).not.toContain("Sub-explorers");
    expect(labels).not.toContain("Decomposition");
    expect(labels).not.toContain("Dimension gen");
    expect(labels).not.toContain("Edge resolution");

    // Orchestrator should have 5 turns (nav-only)
    const orchestrator = result.categories.find(
      (c) => c.label === "Orchestrator",
    )!;
    expect(orchestrator.calls).toBe(5);

    expect(result.breakdown.search_api_calls).toBe(0);
    expect(result.estimated_cost_usd).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // Scaling
  // ---------------------------------------------------------------------------

  it("returns higher cost with higher explore budget", () => {
    const low = estimateCost({
      navBudget: 10,
      exploreBudget: 5,
      models: DEFAULT_MODELS,
    });

    const high = estimateCost({
      navBudget: 10,
      exploreBudget: 50,
      models: DEFAULT_MODELS,
    });

    expect(high.estimated_cost_usd).toBeGreaterThan(low.estimated_cost_usd);
  });

  it("sub-explorer count scales with explore budget", () => {
    const small = estimateCost({
      navBudget: 10,
      exploreBudget: 4,
      models: DEFAULT_MODELS,
    });
    const large = estimateCost({
      navBudget: 10,
      exploreBudget: 20,
      models: DEFAULT_MODELS,
    });

    const smallSubs = small.categories.find(
      (c) => c.label === "Sub-explorers",
    )!;
    const largeSubs = large.categories.find(
      (c) => c.label === "Sub-explorers",
    )!;

    expect(largeSubs.calls).toBeGreaterThan(smallSubs.calls);
  });

  // ---------------------------------------------------------------------------
  // Model roles
  // ---------------------------------------------------------------------------

  it("uses modelRoles to assign different models per role", () => {
    const result = estimateCost({
      navBudget: 100,
      exploreBudget: 10,
      models: DEFAULT_MODELS,
      modelRoles: SAMPLE_ROLES,
    });

    const orchestrator = result.categories.find(
      (c) => c.label === "Orchestrator",
    )!;
    const decomposition = result.categories.find(
      (c) => c.label === "Decomposition",
    )!;

    expect(orchestrator.model_id).toBe("openrouter/x-ai/grok-4.1-fast");
    expect(decomposition.model_id).toBe(
      "openrouter/google/gemini-2.0-flash-001",
    );
  });

  it("falls back to first model when no modelRoles provided", () => {
    const result = estimateCost({
      navBudget: 100,
      exploreBudget: 10,
      models: DEFAULT_MODELS,
    });

    // All categories should use the first model
    for (const cat of result.categories) {
      expect(cat.model_id).toBe("openrouter/x-ai/grok-4.1-fast");
    }
  });

  // ---------------------------------------------------------------------------
  // Unsupported models
  // ---------------------------------------------------------------------------

  it("tracks unsupported models", () => {
    const unknownRoles: ModelRoles = {
      orchestrator: "openrouter/unknown/mystery-model",
      sub_explorer: "openrouter/x-ai/grok-4.1-fast",
      edge_resolution: "openrouter/x-ai/grok-4.1-fast",
      decomposition: "openrouter/another/unknown-model",
      dimension: "openrouter/x-ai/grok-4.1-fast",
      synthesis: "openrouter/x-ai/grok-4.1-fast",
    };

    const result = estimateCost({
      navBudget: 100,
      exploreBudget: 10,
      models: DEFAULT_MODELS,
      modelRoles: unknownRoles,
    });

    expect(result.unsupported_models).toContain(
      "openrouter/unknown/mystery-model",
    );
    expect(result.unsupported_models).toContain(
      "openrouter/another/unknown-model",
    );
    expect(result.unsupported_models).not.toContain(
      "openrouter/x-ai/grok-4.1-fast",
    );
    // Should still compute a cost using default pricing
    expect(result.estimated_cost_usd).toBeGreaterThan(0);
  });

  it("returns empty unsupported_models for known models", () => {
    const result = estimateCost({
      navBudget: 100,
      exploreBudget: 10,
      models: DEFAULT_MODELS,
      modelRoles: SAMPLE_ROLES,
    });

    expect(result.unsupported_models).toEqual([]);
  });

  // ---------------------------------------------------------------------------
  // Backward compat
  // ---------------------------------------------------------------------------

  it("handles empty models array gracefully", () => {
    const result = estimateCost({
      navBudget: 5,
      exploreBudget: 5,
      models: [],
    });

    // Should still compute costs using default pricing
    expect(result.estimated_cost_usd).toBeGreaterThan(0);
    expect(result.categories.length).toBeGreaterThan(0);
  });

  // ---------------------------------------------------------------------------
  // Brave Search cost
  // ---------------------------------------------------------------------------

  it("includes Brave Search cost proportional to explore budget", () => {
    const result = estimateCost({
      navBudget: 10,
      exploreBudget: 10,
      models: DEFAULT_MODELS,
    });

    // Brave cost = 10 * $0.005 = $0.05
    // Total should be at least the search cost
    expect(result.estimated_cost_usd).toBeGreaterThanOrEqual(0.05);
  });
});

// ---------------------------------------------------------------------------
// formatCost
// ---------------------------------------------------------------------------

describe("formatCost", () => {
  it('formats very small values as "< $0.001"', () => {
    expect(formatCost(0)).toBe("< $0.001");
    expect(formatCost(0.0001)).toBe("< $0.001");
    expect(formatCost(0.0009)).toBe("< $0.001");
  });

  it("formats normal values as $X.XXX", () => {
    expect(formatCost(0.001)).toBe("$0.001");
    expect(formatCost(0.01)).toBe("$0.010");
    expect(formatCost(0.123)).toBe("$0.123");
    expect(formatCost(1.5)).toBe("$1.500");
    expect(formatCost(12.3456)).toBe("$12.346");
  });
});

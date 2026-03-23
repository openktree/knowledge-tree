import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TimelineControls } from "../TimelineControls";
import type { TimelineControlsProps } from "../TimelineControls";
import type { TimelineEntry } from "@/types";

// Radix Slider uses ResizeObserver which isn't available in jsdom
beforeAll(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeProps(
  overrides: Partial<TimelineControlsProps> = {},
): TimelineControlsProps {
  return {
    position: -1,
    total: 5,
    isPlaying: false,
    speed: 1,
    isScrubbing: false,
    currentEntry: null,
    onSeek: vi.fn(),
    onStepForward: vi.fn(),
    onStepBackward: vi.fn(),
    onTogglePlay: vi.fn(),
    onSetSpeed: vi.fn(),
    onGoToLive: vi.fn(),
    ...overrides,
  };
}

function makeEntry(overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    index: 0,
    kind: "node_created",
    timestamp: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TimelineControls", () => {
  it("returns null when total is 0", () => {
    const { container } = render(
      <TimelineControls {...makeProps({ total: 0 })} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders the position counter", () => {
    render(<TimelineControls {...makeProps({ total: 10 })} />);
    // When not scrubbing, displayPos is total (10/10)
    expect(screen.getByText("10/10")).toBeInTheDocument();
  });

  it("shows correct position when scrubbing", () => {
    render(
      <TimelineControls
        {...makeProps({ total: 10, isScrubbing: true, position: 3 })}
      />,
    );
    // displayPos = position + 1 = 4
    expect(screen.getByText("4/10")).toBeInTheDocument();
  });

  it("calls onTogglePlay when play button is clicked", async () => {
    const user = userEvent.setup();
    const onTogglePlay = vi.fn();
    render(
      <TimelineControls {...makeProps({ onTogglePlay })} />,
    );

    await user.click(screen.getByLabelText("Play"));
    expect(onTogglePlay).toHaveBeenCalledOnce();
  });

  it("shows Pause label when isPlaying is true", () => {
    render(<TimelineControls {...makeProps({ isPlaying: true })} />);
    expect(screen.getByLabelText("Pause")).toBeInTheDocument();
  });

  it("calls onStepForward when step forward is clicked", async () => {
    const user = userEvent.setup();
    const onStepForward = vi.fn();
    render(
      <TimelineControls
        {...makeProps({
          isScrubbing: true,
          position: 2,
          total: 5,
          onStepForward,
        })}
      />,
    );

    await user.click(screen.getByLabelText("Step forward"));
    expect(onStepForward).toHaveBeenCalledOnce();
  });

  it("calls onStepBackward when step backward is clicked", async () => {
    const user = userEvent.setup();
    const onStepBackward = vi.fn();
    render(
      <TimelineControls
        {...makeProps({
          isScrubbing: true,
          position: 2,
          total: 5,
          onStepBackward,
        })}
      />,
    );

    await user.click(screen.getByLabelText("Step backward"));
    expect(onStepBackward).toHaveBeenCalledOnce();
  });

  it("shows LIVE button only when scrubbing", () => {
    const { rerender } = render(
      <TimelineControls {...makeProps({ isScrubbing: false })} />,
    );
    expect(screen.queryByLabelText("Go to live")).not.toBeInTheDocument();

    rerender(
      <TimelineControls
        {...makeProps({ isScrubbing: true, position: 2 })}
      />,
    );
    expect(screen.getByLabelText("Go to live")).toBeInTheDocument();
  });

  it("calls onGoToLive when LIVE button is clicked", async () => {
    const user = userEvent.setup();
    const onGoToLive = vi.fn();
    render(
      <TimelineControls
        {...makeProps({
          isScrubbing: true,
          position: 2,
          onGoToLive,
        })}
      />,
    );

    await user.click(screen.getByLabelText("Go to live"));
    expect(onGoToLive).toHaveBeenCalledOnce();
  });

  it("displays the current event badge when entry is provided", () => {
    const entry = makeEntry({
      kind: "node_created",
      node: {
        id: "n1",
        concept: "Quantum",
        node_type: "concept",
        entity_subtype: null,
        parent_id: null,
        parent_concept: null,
        attractor: null,
        filter_id: null,
        max_content_tokens: 4000,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        update_count: 0,
        access_count: 0,
        fact_count: 0,
        seed_fact_count: 0,
        pending_facts: 0,
        richness: 0,
        convergence_score: 0,
        definition: null,
        definition_generated_at: null,
        enrichment_status: null,
        metadata: null,
      },
    });

    render(
      <TimelineControls
        {...makeProps({
          isScrubbing: true,
          position: 0,
          currentEntry: entry,
        })}
      />,
    );

    expect(screen.getByText("Created")).toBeInTheDocument();
    expect(screen.getByText("Quantum")).toBeInTheDocument();
  });

  it("displays the current speed", () => {
    render(<TimelineControls {...makeProps({ speed: 2 })} />);
    expect(screen.getByText("2x")).toBeInTheDocument();
  });

  it("calls onSetSpeed with next speed when speed button is clicked", async () => {
    const user = userEvent.setup();
    const onSetSpeed = vi.fn();
    render(
      <TimelineControls {...makeProps({ speed: 1, onSetSpeed })} />,
    );

    await user.click(screen.getByLabelText("Change speed"));
    // nextSpeed(1) = 2
    expect(onSetSpeed).toHaveBeenCalledWith(2);
  });
});

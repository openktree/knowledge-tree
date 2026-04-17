import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphReadOnlyBanner } from "../GraphReadOnlyBanner";
import type { GraphResponse } from "@/types";

type BannerGraph = Pick<
  GraphResponse,
  "read_only" | "read_only_reason" | "graph_type_info" | "graph_type_version"
>;

function makeGraph(overrides: Partial<BannerGraph> = {}): BannerGraph {
  return {
    read_only: true,
    read_only_reason: "owner",
    graph_type_version: 1,
    graph_type_info: { id: "default", display_name: "Default", current_version: 2 },
    ...overrides,
  };
}

describe("GraphReadOnlyBanner", () => {
  it("renders nothing when graph is writable", () => {
    const { container } = render(
      <GraphReadOnlyBanner graph={makeGraph({ read_only: false })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows owner copy when read_only_reason='owner'", () => {
    render(<GraphReadOnlyBanner graph={makeGraph({ read_only_reason: "owner" })} />);
    expect(screen.getByText(/Graph is read-only/i)).toBeInTheDocument();
    expect(screen.getByText(/owner has set this graph/i)).toBeInTheDocument();
  });

  it("shows migration copy with target version", () => {
    render(
      <GraphReadOnlyBanner graph={makeGraph({ read_only_reason: "migrating" })} />,
    );
    expect(screen.getByText(/Graph is migrating/i)).toBeInTheDocument();
    expect(screen.getByText(/to v2/i)).toBeInTheDocument();
  });

  it("shows error copy when read_only_reason='error'", () => {
    render(<GraphReadOnlyBanner graph={makeGraph({ read_only_reason: "error" })} />);
    expect(screen.getByText(/Migration failed/i)).toBeInTheDocument();
    expect(screen.getByText(/re-dispatch the migration/i)).toBeInTheDocument();
  });

  it("falls back to owner copy when reason is null", () => {
    render(<GraphReadOnlyBanner graph={makeGraph({ read_only_reason: null })} />);
    expect(screen.getByText(/Graph is read-only/i)).toBeInTheDocument();
  });
});

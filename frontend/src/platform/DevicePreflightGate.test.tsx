import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { evaluateDevicePreflight } from "./devicePreflight";
import { DevicePreflightGate } from "./DevicePreflightGate";

vi.mock("./devicePreflight", () => ({ evaluateDevicePreflight: vi.fn() }));

describe("DevicePreflightGate", () => {
  beforeEach(() => vi.mocked(evaluateDevicePreflight).mockReset());

  it("blocks the workspace with explicit reasons", () => {
    vi.mocked(evaluateDevicePreflight).mockReturnValue({
      blockers: ["unsupported_browser", "screen_too_narrow"],
      warnings: [],
    });
    render(
      <DevicePreflightGate>
        <p>workspace</p>
      </DevicePreflightGate>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("桌面 Chrome 或 Edge");
    expect(screen.getByRole("alert")).toHaveTextContent("1024px");
    expect(screen.queryByText("workspace")).not.toBeInTheDocument();
  });

  it("shows non-blocking capability warnings and keeps the POC available", () => {
    vi.mocked(evaluateDevicePreflight).mockReturnValue({
      blockers: [],
      warnings: ["indexed_db_unavailable", "webgl_unavailable"],
    });
    render(
      <DevicePreflightGate>
        <p>workspace</p>
      </DevicePreflightGate>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("IndexedDB");
    expect(screen.getByRole("status")).toHaveTextContent("WebGL");
    expect(screen.getByText("workspace")).toBeInTheDocument();
  });
});

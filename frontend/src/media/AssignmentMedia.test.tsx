import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssignmentMedia } from "./AssignmentMedia";

function jsonResponse(body: object): Response {
  return { json: async () => body, ok: true, status: 200 } as Response;
}

const variants = [
  {
    coordinate_mapping: "normalized_identity",
    expires_at: "2026-08-09T12:05:00Z",
    height: 1024,
    media_variant_id: "compressed-001",
    role: "compressed",
    url: "https://private.cos.test/compressed.jpg?renewal=1",
    width: 2048,
  },
  {
    coordinate_mapping: "normalized_identity",
    expires_at: "2026-08-09T12:05:00Z",
    height: 2048,
    media_variant_id: "high-001",
    role: "high_resolution",
    url: "https://private.cos.test/high.png?renewal=1",
    width: 4096,
  },
] as const;

function mediaPayload(currentVariants: readonly object[] = variants) {
  return {
    assignment_id: "assignment-001",
    expires_at: "2026-08-09T12:05:00Z",
    unavailable_roles: [],
    variants: currentVariants,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AssignmentMedia", () => {
  it("PAP-MID-SC-010 displays compressed first, then switches without changing mapping", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(mediaPayload())));
    render(<AssignmentMedia assignmentId="assignment-001" />);

    const compressed = await screen.findByRole("img", { name: "当前任务全景图（压缩）" });
    expect(compressed).toHaveAttribute("src", variants[0].url);
    expect(screen.queryByAltText("当前任务全景图（高清）")).not.toBeInTheDocument();

    fireEvent.load(compressed);
    const high = await screen.findByAltText("当前任务全景图（高清）");
    fireEvent.load(high);
    expect(screen.getByText("当前媒体：压缩图")).toBeInTheDocument();
    expect(screen.getByTestId("media-coordinate-space")).toHaveAttribute(
      "data-coordinate-mapping",
      "normalized_identity",
    );

    fireEvent.click(screen.getByRole("button", { name: "切换到高清图" }));
    expect(screen.getByText("当前媒体：高清图")).toBeInTheDocument();
    expect(screen.getByTestId("media-coordinate-space")).toHaveAttribute(
      "data-coordinate-mapping",
      "normalized_identity",
    );
  });

  it("PAP-MID-SC-009 PAP-MID-SC-011 renews an expired URL once, then falls back", async () => {
    const expired = variants.map((variant) => ({ ...variant, url: `${variant.url}-expired` }));
    const renewed = variants.map((variant) => ({ ...variant, url: `${variant.url}-renewed` }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(mediaPayload(expired)))
      .mockResolvedValueOnce(jsonResponse(mediaPayload(renewed)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AssignmentMedia assignmentId="assignment-001" />);

    const compressed = await screen.findByRole("img", { name: "当前任务全景图（压缩）" });
    fireEvent.error(compressed);
    await waitFor(() =>
      expect(screen.getByAltText("当前任务全景图（压缩）")).toHaveAttribute("src", renewed[0].url),
    );
    fireEvent.error(screen.getByAltText("当前任务全景图（压缩）"));
    const high = await screen.findByAltText("当前任务全景图（高清）");
    fireEvent.load(high);

    expect(await screen.findByText("当前媒体：高清图（降级）")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("PAP-MID-SC-012 blocks the task when both variants fail after renewal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(mediaPayload()));
    vi.stubGlobal("fetch", fetchMock);
    render(<AssignmentMedia assignmentId="assignment-001" />);

    const compressed = await screen.findByRole("img", { name: "当前任务全景图（压缩）" });
    fireEvent.error(compressed);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.error(compressed);
    const high = await screen.findByAltText("当前任务全景图（高清）");
    fireEvent.error(high);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    fireEvent.error(high);

    expect(await screen.findByRole("alert")).toHaveTextContent("image_unavailable");
    expect(screen.getByRole("button", { name: "重试加载媒体" })).toBeInTheDocument();
  });

  it("PAP-MID-SC-011 keeps compressed media when high resolution fails", async () => {
    const renewed = variants.map((variant) => ({ ...variant, url: `${variant.url}-renewed` }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(mediaPayload()))
      .mockResolvedValueOnce(jsonResponse(mediaPayload(renewed)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AssignmentMedia assignmentId="assignment-001" />);

    const compressed = await screen.findByAltText("当前任务全景图（压缩）");
    fireEvent.load(compressed);
    fireEvent.error(await screen.findByAltText("当前任务全景图（高清）"));
    await waitFor(() =>
      expect(screen.getByAltText("当前任务全景图（高清）")).toHaveAttribute("src", renewed[1].url),
    );
    expect(screen.getByAltText("当前任务全景图（压缩）")).toHaveAttribute("src", variants[0].url);
    fireEvent.error(screen.getByAltText("当前任务全景图（高清）"));

    expect(await screen.findByText("当前媒体：压缩图（降级）")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses a single allowed variant without inventing a replacement", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(mediaPayload([variants[1]]))));
    render(<AssignmentMedia assignmentId="assignment-001" />);

    const high = await screen.findByRole("img", { name: "当前任务全景图（高清）" });
    fireEvent.load(high);

    expect(screen.getByText("当前媒体：高清图")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /切换到/ })).not.toBeInTheDocument();
  });
});

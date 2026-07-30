import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MediaImportWizard } from "./MediaImportWizard";

const highCandidate = {
  content_crc64ecma: "12345678901234567890",
  content_length: 8_388_608,
  content_sha256: "a".repeat(64),
  coordinate_mapping: "normalized_identity",
  format: "png",
  height: 2048,
  object_version: "cos-version-high-001",
  preview_url: "https://cos.example.test/high.png?signature=redacted",
  source_key: "incoming/warehouse-001/high.png",
  width: 4096,
} as const;

const compressedCandidate = {
  content_crc64ecma: "1234567890",
  content_length: 1_048_576,
  content_sha256: "b".repeat(64),
  coordinate_mapping: "normalized_identity",
  format: "jpeg",
  height: 1024,
  object_version: "cos-version-compressed-001",
  preview_url: "https://cos.example.test/compressed.jpg?signature=redacted",
  source_key: "incoming/warehouse-001/compressed.jpg",
  width: 2048,
} as const;

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

function previewPayload(id = "preview-001", createAnnotationRound = false) {
  return {
    asset_source_key: "panoramas/warehouse-001",
    asset_will_be_reused: false,
    create_annotation_round: createAnnotationRound,
    expires_at: "2026-07-30T12:00:00Z",
    plan_sha256: createAnnotationRound ? "d".repeat(64) : "c".repeat(64),
    preview_id: id,
    variants: [
      { ...highCandidate, role: "high_resolution" },
      { ...compressedCandidate, role: "compressed" },
    ],
  };
}

async function browseAndSelectPair(): Promise<void> {
  fireEvent.change(screen.getByLabelText("Asset source key"), {
    target: { value: "panoramas/warehouse-001" },
  });
  fireEvent.change(screen.getByLabelText("COS 前缀"), {
    target: { value: "incoming/warehouse-001" },
  });
  fireEvent.click(screen.getByRole("button", { name: "浏览 COS 候选" }));
  await screen.findAllByRole("option", { name: highCandidate.source_key });
  fireEvent.change(screen.getByLabelText("高清候选"), {
    target: { value: highCandidate.source_key },
  });
  fireEvent.change(screen.getByLabelText("压缩候选"), {
    target: { value: compressedCandidate.source_key },
  });
}

describe("MediaImportWizard", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=component-test-token; Path=/";
  });

  afterEach(() => {
    cleanup();
    document.cookie = "csrftoken=; Max-Age=0; Path=/";
    vi.restoreAllMocks();
  });

  it("paginates registered candidates, prevents duplicate roles, and invalidates stale previews", async () => {
    const extraCandidate = {
      ...compressedCandidate,
      object_version: "cos-version-compressed-002",
      source_key: "incoming/warehouse-001/compressed-v2.jpg",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ candidates: [highCandidate, compressedCandidate], next_marker: "page-1" }),
      )
      .mockResolvedValueOnce(jsonResponse({ candidates: [extraCandidate], next_marker: null }))
      .mockResolvedValueOnce(jsonResponse(previewPayload()));
    vi.stubGlobal("fetch", fetchMock);
    render(<MediaImportWizard />);

    await browseAndSelectPair();
    const compressedSelect = screen.getByLabelText("压缩候选");
    expect(
      within(compressedSelect).getByRole("option", { name: highCandidate.source_key }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await screen.findAllByRole("option", { name: extraCandidate.source_key });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/media/candidates?prefix=incoming%2Fwarehouse-001&marker=page-1",
      expect.objectContaining({ credentials: "same-origin" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "预览配对" }));
    expect(await screen.findByRole("region", { name: "媒体配对预览" })).toBeInTheDocument();
    expect(screen.getByText(`SHA-256: ${highCandidate.content_sha256}`)).toBeInTheDocument();
    expect(screen.getByText("尺寸: 4096 × 2048")).toBeInTheDocument();
    expect(screen.getByText("格式: PNG")).toBeInTheDocument();
    expect(screen.getAllByText("坐标映射: normalized_identity")).toHaveLength(2);
    expect(screen.getByText("将创建新的 Asset。")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Asset source key"), {
      target: { value: "panoramas/warehouse-001-edited" },
    });
    expect(screen.queryByRole("region", { name: "媒体配对预览" })).not.toBeInTheDocument();
  });

  it("cancels on the backend and publishes only the frozen preview once", async () => {
    let resolvePublish: ((response: Response) => void) | undefined;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ candidates: [highCandidate, compressedCandidate], next_marker: null }),
      )
      .mockResolvedValueOnce(jsonResponse(previewPayload("preview-cancel")))
      .mockResolvedValueOnce(jsonResponse({ cancelled: true, preview_id: "preview-cancel" }))
      .mockResolvedValueOnce(jsonResponse(previewPayload("preview-publish", true)))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolvePublish = resolve;
          }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(<MediaImportWizard />);

    await browseAndSelectPair();
    fireEvent.click(screen.getByRole("button", { name: "预览配对" }));
    await screen.findByRole("region", { name: "媒体配对预览" });
    fireEvent.click(screen.getByRole("button", { name: "取消预览" }));
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "媒体配对预览" })).not.toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/admin/media/imports/cancel",
      expect.objectContaining({
        body: JSON.stringify({
          expected_plan_sha256: "c".repeat(64),
          preview_id: "preview-cancel",
        }),
        method: "POST",
      }),
    );

    fireEvent.click(screen.getByLabelText("请求新轮次"));
    fireEvent.click(screen.getByRole("button", { name: "预览配对" }));
    await screen.findByRole("region", { name: "媒体配对预览" });
    expect(
      screen.getByText("将创建 Manual Task；若该 Asset 尚无 Task，它将成为首轮。"),
    ).toBeInTheDocument();
    const publishButton = screen.getByRole("button", { name: "发布媒体并创建 Task" });
    fireEvent.click(publishButton);
    fireEvent.click(publishButton);
    expect(publishButton).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/api/admin/media/imports/publish",
      expect.objectContaining({
        body: JSON.stringify({
          expected_plan_sha256: "d".repeat(64),
          preview_id: "preview-publish",
        }),
        method: "POST",
      }),
    );

    resolvePublish?.(
      jsonResponse({
        annotation_round: {
          asset_id: "asset-001",
          mode: "manual",
          previous_task_id: null,
          status: "published",
          task_id: "task-001",
        },
        asset_id: "asset-001",
        created_asset: true,
        media_variants: [
          { created: true, media_variant_id: "variant-high-001", role: "high_resolution" },
          { created: false, media_variant_id: "variant-compressed-001", role: "compressed" },
        ],
      }),
    );
    expect(await screen.findByText(/Asset ID: asset-001/)).toBeInTheDocument();
    expect(
      screen.getByText(/已创建 high_resolution MediaVariant ID: variant-high-001/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/已复用 compressed MediaVariant ID: variant-compressed-001/),
    ).toBeInTheDocument();
    expect(screen.getByText(/已创建 Manual Task。Task ID: task-001/)).toBeInTheDocument();
    expect(screen.getByText("这是该 Asset 的首轮 Task，无上一轮。")).toBeInTheDocument();
  });

  it("renders stable Chinese guidance for backend integrity errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ error: { code: "media_candidate_integrity_conflict" } }, 409),
        ),
    );
    render(<MediaImportWizard />);

    fireEvent.click(screen.getByRole("button", { name: "浏览 COS 候选" }));

    expect(
      await screen.findByText("COS 对象完整性校验失败，请重新登记该版本。"),
    ).toBeInTheDocument();
  });

  it.each([
    [
      "invalid panorama ratio",
      "media_panorama_aspect_ratio_invalid",
      "全景图必须是已拼接的 2:1 等距柱状图。",
    ],
    [
      "PAP-MID-SC-004 skybox rejection",
      "media_skybox_not_supported",
      "首版不接收 skybox 面集合，请先在平台外完成拼接。",
    ],
  ])("renders stable Chinese guidance for %s", async (_caseName, code, message) => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse({ candidates: [highCandidate, compressedCandidate], next_marker: null }),
        )
        .mockResolvedValueOnce(jsonResponse({ error: { code } }, 400)),
    );
    render(<MediaImportWizard />);

    await browseAndSelectPair();
    fireEvent.click(screen.getByRole("button", { name: "预览配对" }));

    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});

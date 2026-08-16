import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PredictionImportWizard } from "./PredictionImportWizard";

function jsonResponse(body: object, status = 200): Response {
  return { json: async () => body, ok: status >= 200 && status < 300, status } as Response;
}

describe("PredictionImportWizard", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  it("PAP-PAS-SC-003 PAP-PAS-SC-005 PAP-PAS-SC-006 previews a local layout, freezes it, and creates a Semi Task without a model runtime", async () => {
    document.cookie = "csrftoken=test-csrf; path=/";
    const layoutText = JSON.stringify({
      image_filename: "room.jpg",
      image_size: [20, 10],
      layout: {
        corners: [
          { id: 0, x: 4, y_ceiling: 1, y_floor: 9 },
          { id: 1, x: 12, y_ceiling: 2, y_floor: 8 },
        ],
        num_corners: 2,
        order: "sorted_x_cyclic",
      },
      meta: { config: "local.yaml" },
    });
    const fetchMock = vi
      .fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(
        jsonResponse(
          {
            asset_id: "asset-001",
            expires_at: "2026-08-10T12:00:00Z",
            importer_version: "panorama-layout-json-v1",
            preview_id: "preview-001",
            media_variants: [
              { media_variant_id: "variant-001", role: "compressed" },
              { media_variant_id: "variant-002", role: "high_resolution" },
            ],
            preview_media: {
              coordinate_mapping: "normalized_identity",
              height: 10,
              media_variant_id: "variant-001",
              role: "compressed",
              url: "https://cos.test/room.jpg?versionId=v1",
              width: 20,
            },
            raw_output_sha256: "a".repeat(64),
            state: {
              pairs: [
                {
                  bottom: { point_id: "bottom-1", u: 0.2, v: 0.9 },
                  order_index: 0,
                  pair_id: "pair-1",
                  top: { point_id: "top-1", u: 0.2, v: 0.1 },
                },
              ],
            },
            state_sha256: "b".repeat(64),
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            artifact_id: "artifact-001",
            artifact_sha256: "c".repeat(64),
            asset_id: "asset-001",
            created_at: "2026-08-10T12:01:00Z",
            state_sha256: "b".repeat(64),
          },
          201,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { mode: "semi", reused: true, status: "published", task_id: "semi-task-001" },
          200,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File([layoutText], "layout.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => layoutText });

    render(<PredictionImportWizard />);

    fireEvent.change(screen.getByLabelText("Asset ID"), { target: { value: "asset-001" } });
    fireEvent.change(screen.getByLabelText("模型名称 / Model name"), {
      target: { value: "local-panorama-model" },
    });
    fireEvent.change(screen.getByLabelText("模型版本 / Model version"), {
      target: { value: "local-v1" },
    });
    fireEvent.change(screen.getByLabelText("Checkpoint SHA-256"), {
      target: { value: "d".repeat(64) },
    });
    fireEvent.change(screen.getByLabelText("本地 layout JSON / Local layout JSON"), {
      target: { files: [file] },
    });
    fireEvent.submit(screen.getByRole("button", { name: "预览 / Preview" }).closest("form")!);

    const image = await screen.findByLabelText("预测叠图 / Prediction overlay");
    expect(image).toHaveAttribute("href", "https://cos.test/room.jpg?versionId=v1");
    const line = document.querySelector("line");
    expect(line).toHaveAttribute("x1", "0.2");
    expect(line).toHaveAttribute("y1", "0.1");
    expect(screen.queryByRole("textbox", { name: /script/i })).not.toBeInTheDocument();
    expect(screen.getByText(/compressed · variant-001/)).toBeInTheDocument();
    expect(screen.getByText(/high_resolution · variant-002/)).toBeInTheDocument();

    const previewCall = fetchMock.mock.calls[0];
    expect(previewCall?.[0]).toBe("/api/admin/predictions/preview");
    expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
      asset_id: "asset-001",
      checkpoint_sha256: "d".repeat(64),
      import_source: "local-admin-upload",
      importer_version: "panorama-layout-json-v1",
      layout_text: layoutText,
      model_name: "local-panorama-model",
      model_version: "local-v1",
    });

    fireEvent.click(screen.getByRole("button", { name: "冻结 Artifact / Freeze artifact" }));
    expect(await screen.findByText(/artifact-001/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/admin/predictions/preview-001/publish");

    fireEvent.click(
      screen.getByRole("button", {
        name: "创建并发布 Semi Task / Create and publish Semi Task",
      }),
    );
    expect(await screen.findByText(/已复用现有 Semi Task.*semi-task-001/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/api/admin/predictions/artifact-001/semi-tasks");
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      body: JSON.stringify({ media_variant_ids: ["variant-001", "variant-002"] }),
      method: "POST",
    });
  });
});

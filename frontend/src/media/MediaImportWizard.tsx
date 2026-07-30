import { useState } from "react";

import { apiFetch } from "../api";

type MediaCandidate = {
  content_crc64ecma: string;
  content_length: number;
  content_sha256: string;
  coordinate_mapping: "normalized_identity";
  format: "jpeg" | "png";
  height: number;
  object_version: string;
  preview_url: string;
  source_key: string;
  width: number;
};

type CandidateResponse = {
  candidates: MediaCandidate[];
  next_marker: string | null;
};

type PreviewVariant = MediaCandidate & {
  role: "compressed" | "high_resolution";
};

type ImportPreview = {
  asset_source_key: string;
  asset_will_be_reused: boolean;
  create_annotation_round: boolean;
  expires_at: string;
  plan_sha256: string;
  preview_id: string;
  variants: PreviewVariant[];
};

type Publication = {
  annotation_round: {
    asset_id: string;
    mode: "manual";
    previous_task_id: string | null;
    status: "published";
    task_id: string;
  } | null;
  asset_id: string;
  created_asset: boolean;
  media_variants: {
    created: boolean;
    media_variant_id: string;
    role: PreviewVariant["role"];
  }[];
};

const ERROR_MESSAGES: Record<string, string> = {
  authentication_required: "登录已失效，请重新登录。",
  cos_unavailable: "COS 暂时不可用，请稍后重试。",
  invalid_media_import: "媒体导入参数无效，请检查配对。",
  media_candidate_integrity_conflict: "COS 对象完整性校验失败，请重新登记该版本。",
  media_candidate_invalid: "COS 候选元数据无效。",
  media_candidate_not_found: "COS 候选不存在或已删除。",
  media_format_unsupported: "媒体格式不受支持。",
  media_import_conflict: "媒体导入与现有数据冲突。",
  media_import_integrity_conflict: "预览后的 COS 对象信息已变化，请重新预览。",
  media_import_preview_cancelled: "该预览已取消。",
  media_import_preview_expired: "该预览已过期，请重新预览。",
  media_import_preview_not_found: "找不到该预览。",
  media_panorama_aspect_ratio_invalid: "全景图必须是已拼接的 2:1 等距柱状图。",
  media_skybox_not_supported: "首版不接收 skybox 面集合，请先在平台外完成拼接。",
  media_source_key_conflict: "该 source key 已绑定不同内容。",
  media_variant_mapping_incompatible: "媒体变体无法保持相同的规范化坐标语义。",
};

async function errorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: { code?: string } };
    const code = payload.error?.code;
    return code === undefined ? fallback : (ERROR_MESSAGES[code] ?? fallback);
  } catch {
    return fallback;
  }
}

function selectedPreview(
  preview: ImportPreview | null,
  role: PreviewVariant["role"],
): PreviewVariant | undefined {
  return preview?.variants.find((variant) => variant.role === role);
}

function VariantDetails({ variant }: { variant: PreviewVariant }) {
  const role = variant.role === "high_resolution" ? "高清" : "压缩";
  return (
    <figure>
      <img alt={`${role}候选预览`} src={variant.preview_url} />
      <figcaption>
        <strong>{role}变体</strong>
        <span>source key: {variant.source_key}</span>
        <span>SHA-256: {variant.content_sha256}</span>
        <span>对象版本: {variant.object_version}</span>
        <span>
          尺寸: {variant.width} × {variant.height}
        </span>
        <span>格式: {variant.format.toUpperCase()}</span>
        <span>坐标映射: {variant.coordinate_mapping}</span>
      </figcaption>
    </figure>
  );
}

export function MediaImportWizard() {
  const [assetSourceKey, setAssetSourceKey] = useState("");
  const [prefix, setPrefix] = useState("");
  const [candidates, setCandidates] = useState<MediaCandidate[]>([]);
  const [nextMarker, setNextMarker] = useState<string | null>(null);
  const [highResolutionSourceKey, setHighResolutionSourceKey] = useState("");
  const [compressedSourceKey, setCompressedSourceKey] = useState("");
  const [createAnnotationRound, setCreateAnnotationRound] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [publication, setPublication] = useState<Publication | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  function invalidatePreview(): void {
    setPreview(null);
    setPublication(null);
    setError(null);
  }

  const importRequest = () => ({
    asset_source_key: assetSourceKey,
    compressed_source_key: compressedSourceKey,
    create_annotation_round: createAnnotationRound,
    high_resolution_source_key: highResolutionSourceKey,
  });

  async function browseCandidates(marker: string | null = null) {
    setError(null);
    setPublication(null);
    setPreview(null);
    setIsLoading(true);
    const query = new URLSearchParams({ prefix });
    if (marker !== null) {
      query.set("marker", marker);
    }
    try {
      const response = await apiFetch(`/api/admin/media/candidates?${query.toString()}`);
      if (!response.ok) {
        throw new Error(await errorMessage(response, "COS 候选加载失败。"));
      }
      const payload = (await response.json()) as CandidateResponse;
      setCandidates((current) =>
        marker === null ? payload.candidates : [...current, ...payload.candidates],
      );
      setNextMarker(payload.next_marker);
      if (marker === null) {
        setHighResolutionSourceKey("");
        setCompressedSourceKey("");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "COS 候选加载失败。");
    } finally {
      setIsLoading(false);
    }
  }

  async function previewPair() {
    if (!assetSourceKey || !highResolutionSourceKey || !compressedSourceKey) {
      setError("请选择 Asset source key、高清候选和压缩候选。");
      return;
    }
    if (highResolutionSourceKey === compressedSourceKey) {
      setError("高清与压缩变体必须选择不同候选。");
      return;
    }
    setError(null);
    setPublication(null);
    setIsLoading(true);
    try {
      const response = await apiFetch("/api/admin/media/imports/preview", {
        body: JSON.stringify(importRequest()),
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, "媒体配对预览失败。"));
      }
      setPreview((await response.json()) as ImportPreview);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "媒体配对预览失败。");
    } finally {
      setIsLoading(false);
    }
  }

  async function cancelPreview() {
    if (preview === null) {
      return;
    }
    setError(null);
    setIsLoading(true);
    try {
      const response = await apiFetch("/api/admin/media/imports/cancel", {
        body: JSON.stringify({
          expected_plan_sha256: preview.plan_sha256,
          preview_id: preview.preview_id,
        }),
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, "取消媒体预览失败。"));
      }
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消媒体预览失败。");
    } finally {
      setIsLoading(false);
    }
  }

  async function publishImport() {
    if (preview === null || isLoading) {
      return;
    }
    setError(null);
    setIsLoading(true);
    try {
      const response = await apiFetch("/api/admin/media/imports/publish", {
        body: JSON.stringify({
          expected_plan_sha256: preview.plan_sha256,
          preview_id: preview.preview_id,
        }),
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, "媒体发布失败。"));
      }
      setPublication((await response.json()) as Publication);
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "媒体发布失败。");
    } finally {
      setIsLoading(false);
    }
  }

  const highResolutionPreview = selectedPreview(preview, "high_resolution");
  const compressedPreview = selectedPreview(preview, "compressed");

  return (
    <section aria-labelledby="media-import-heading" className="media-import-wizard">
      <div>
        <p className="eyebrow">管理员工作流</p>
        <h2 id="media-import-heading">管理员 COS 媒体导入</h2>
        <p>只浏览可信 manifest 已登记的对象版本，配对后冻结预览再发布。</p>
      </div>

      <div className="media-import-fields">
        <label>
          Asset source key
          <input
            onChange={(event) => {
              invalidatePreview();
              setAssetSourceKey(event.target.value);
            }}
            placeholder="panoramas/warehouse-001"
            value={assetSourceKey}
          />
        </label>
        <label>
          COS 前缀
          <input
            onChange={(event) => {
              invalidatePreview();
              setPrefix(event.target.value);
            }}
            placeholder="incoming/warehouse-001"
            value={prefix}
          />
        </label>
        <button disabled={isLoading} onClick={() => void browseCandidates()} type="button">
          浏览 COS 候选
        </button>
        {nextMarker ? (
          <button
            disabled={isLoading}
            onClick={() => void browseCandidates(nextMarker)}
            type="button"
          >
            加载更多
          </button>
        ) : null}
      </div>

      {candidates.length > 0 ? (
        <div className="media-import-fields">
          <label>
            高清候选
            <select
              onChange={(event) => {
                invalidatePreview();
                setHighResolutionSourceKey(event.target.value);
              }}
              value={highResolutionSourceKey}
            >
              <option value="">选择高清候选</option>
              {candidates.map((candidate) => (
                <option
                  disabled={candidate.source_key === compressedSourceKey}
                  key={candidate.source_key}
                  value={candidate.source_key}
                >
                  {candidate.source_key}
                </option>
              ))}
            </select>
          </label>
          <label>
            压缩候选
            <select
              onChange={(event) => {
                invalidatePreview();
                setCompressedSourceKey(event.target.value);
              }}
              value={compressedSourceKey}
            >
              <option value="">选择压缩候选</option>
              {candidates.map((candidate) => (
                <option
                  disabled={candidate.source_key === highResolutionSourceKey}
                  key={candidate.source_key}
                  value={candidate.source_key}
                >
                  {candidate.source_key}
                </option>
              ))}
            </select>
          </label>
          <label className="media-import-checkbox">
            <input
              aria-label="请求新轮次"
              checked={createAnnotationRound}
              onChange={(event) => {
                invalidatePreview();
                setCreateAnnotationRound(event.target.checked);
              }}
              type="checkbox"
            />
            发布媒体后创建新的 Manual 标注轮次
          </label>
          <button disabled={isLoading} onClick={() => void previewPair()} type="button">
            预览配对
          </button>
        </div>
      ) : null}

      {error ? (
        <p className="media-import-error" role="alert">
          {error}
        </p>
      ) : null}

      {preview ? (
        <section aria-label="媒体配对预览" className="media-import-preview">
          <p>{preview.asset_will_be_reused ? "将复用既有 Asset。" : "将创建新的 Asset。"}</p>
          {preview.create_annotation_round ? (
            <p>将创建 Manual Task；若该 Asset 尚无 Task，它将成为首轮。</p>
          ) : null}
          <div className="media-import-images">
            {highResolutionPreview ? <VariantDetails variant={highResolutionPreview} /> : null}
            {compressedPreview ? <VariantDetails variant={compressedPreview} /> : null}
          </div>
          <div className="media-import-actions">
            <button disabled={isLoading} onClick={() => void cancelPreview()} type="button">
              取消预览
            </button>
            <button disabled={isLoading} onClick={() => void publishImport()} type="button">
              {preview.create_annotation_round ? "发布媒体并创建 Task" : "发布媒体"}
            </button>
          </div>
        </section>
      ) : null}

      {publication ? (
        <section aria-label="媒体发布结果" className="media-import-success">
          <p>
            {publication.created_asset ? "已创建 Asset。" : "已复用 Asset。"} Asset ID:{" "}
            {publication.asset_id}
          </p>
          <ul>
            {publication.media_variants.map((variant) => (
              <li key={variant.media_variant_id}>
                {variant.created ? "已创建" : "已复用"} {variant.role} MediaVariant ID:{" "}
                {variant.media_variant_id}
              </li>
            ))}
          </ul>
          {publication.annotation_round ? (
            <div>
              <p>已创建 Manual Task。Task ID: {publication.annotation_round.task_id}</p>
              {publication.annotation_round.previous_task_id ? (
                <p>上一轮 Task ID: {publication.annotation_round.previous_task_id}</p>
              ) : (
                <p>这是该 Asset 的首轮 Task，无上一轮。</p>
              )}
            </div>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

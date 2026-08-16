import { type FormEvent, useState } from "react";

import { apiFetch } from "../api";

const IMPORTER_VERSION = "panorama-layout-json-v1";

const ERROR_MESSAGES: Record<string, string> = {
  invalid_semi_task_request: "Semi Task 媒体选择无效。 / Invalid Semi Task media selection.",
  resource_not_found:
    "所选 Artifact 或媒体不存在。 / The selected artifact or media was not found.",
  semi_task_conflict: "Semi Task 无法发布。 / The Semi Task could not be published.",
  task_media_mismatch: "所选媒体不属于该 Artifact 的 Asset。 / Media belongs to another Asset.",
  task_media_unpublished: "所选媒体尚未发布。 / Selected media is not published.",
};

type Preview = {
  media_variants: {
    media_variant_id: string;
    role: "compressed" | "high_resolution";
  }[];
  preview_id: string;
  preview_media: {
    height: number;
    media_variant_id: string;
    url: string;
    width: number;
  };
  raw_output_sha256: string;
  state: {
    pairs: {
      bottom: { u: number; v: number };
      pair_id: string;
      top: { u: number; v: number };
    }[];
  };
  state_sha256: string;
};

type Artifact = {
  artifact_id: string;
  artifact_sha256: string;
};

type SemiTask = {
  mode: "semi";
  reused: boolean;
  status: "published";
  task_id: string;
};

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: { code?: string } };
    const code = payload.error?.code ?? "prediction_import_failed";
    return ERROR_MESSAGES[code] ?? code;
  } catch {
    return "prediction_import_failed";
  }
}

export function PredictionImportWizard() {
  const [assetId, setAssetId] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelVersion, setModelVersion] = useState("");
  const [checkpointSha256, setCheckpointSha256] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [semiTask, setSemiTask] = useState<SemiTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function createPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null || busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    setArtifact(null);
    setSemiTask(null);
    try {
      const response = await apiFetch("/api/admin/predictions/preview", {
        body: JSON.stringify({
          asset_id: assetId,
          checkpoint_sha256: checkpointSha256,
          import_source: "local-admin-upload",
          importer_version: IMPORTER_VERSION,
          layout_text: await file.text(),
          model_name: modelName,
          model_version: modelVersion,
        }),
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response));
      setPreview((await response.json()) as Preview);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "prediction_import_failed");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (preview === null || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/admin/predictions/${preview.preview_id}/publish`, {
        body: "{}",
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response));
      setArtifact((await response.json()) as Artifact);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "prediction_publication_failed");
    } finally {
      setBusy(false);
    }
  }

  async function createSemiTask() {
    if (artifact === null || preview === null || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/admin/predictions/${artifact.artifact_id}/semi-tasks`, {
        body: JSON.stringify({
          media_variant_ids: preview.media_variants.map((variant) => variant.media_variant_id),
        }),
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response));
      setSemiTask((await response.json()) as SemiTask);
    } catch (reason) {
      setError(
        reason instanceof TypeError
          ? "创建结果尚未确认；可安全重试相同操作。 / Result unknown; retrying the same operation is safe."
          : reason instanceof Error
            ? reason.message
            : "semi_task_publication_failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="prediction-import-heading" className="prediction-import-wizard">
      <h2 id="prediction-import-heading">本地预测导入 / Local prediction import</h2>
      <p>
        仅导入已注册的本地 layout JSON；平台不会执行模型或脚本。 / Registered local layout JSON
        only; the platform runs no model or script.
      </p>
      <form onSubmit={(event) => void createPreview(event)}>
        <label>
          Asset ID
          <input onChange={(event) => setAssetId(event.target.value)} required value={assetId} />
        </label>
        <label>
          模型名称 / Model name
          <input
            onChange={(event) => setModelName(event.target.value)}
            required
            value={modelName}
          />
        </label>
        <label>
          模型版本 / Model version
          <input
            onChange={(event) => setModelVersion(event.target.value)}
            required
            value={modelVersion}
          />
        </label>
        <label>
          Checkpoint SHA-256
          <input
            minLength={64}
            onChange={(event) => setCheckpointSha256(event.target.value)}
            required
            value={checkpointSha256}
          />
        </label>
        <label>
          本地 layout JSON / Local layout JSON
          <input
            accept="application/json,.json"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            required
            type="file"
          />
        </label>
        <button disabled={busy} type="submit">
          预览 / Preview
        </button>
      </form>

      {error ? <p role="alert">{error}</p> : null}

      {preview ? (
        <section aria-label="预测预览 / Prediction preview">
          <svg
            aria-label="预测叠图画布 / Prediction overlay canvas"
            preserveAspectRatio="none"
            role="img"
            viewBox="0 0 1 1"
          >
            <image
              aria-label="预测叠图 / Prediction overlay"
              height="1"
              href={preview.preview_media.url}
              preserveAspectRatio="none"
              width="1"
            />
            {preview.state.pairs.map((pair) => (
              <line
                key={pair.pair_id}
                stroke="red"
                strokeWidth="0.004"
                x1={pair.top.u}
                x2={pair.bottom.u}
                y1={pair.top.v}
                y2={pair.bottom.v}
              />
            ))}
          </svg>
          <p>State SHA-256: {preview.state_sha256}</p>
          <p>Raw output SHA-256: {preview.raw_output_sha256}</p>
          <p>将冻结到 Semi Task 的媒体 / Media to freeze into the Semi Task:</p>
          <ul>
            {preview.media_variants.map((variant) => (
              <li key={variant.media_variant_id}>
                {variant.role} · {variant.media_variant_id}
              </li>
            ))}
          </ul>
          <button disabled={busy || artifact !== null} onClick={() => void publish()} type="button">
            冻结 Artifact / Freeze artifact
          </button>
        </section>
      ) : null}

      {artifact ? (
        <section aria-label="冻结 PredictionArtifact / Frozen PredictionArtifact">
          <p role="status">
            Artifact {artifact.artifact_id} · SHA-256 {artifact.artifact_sha256}
          </p>
          <button
            disabled={busy || semiTask !== null}
            onClick={() => void createSemiTask()}
            type="button"
          >
            创建并发布 Semi Task / Create and publish Semi Task
          </button>
        </section>
      ) : null}

      {semiTask ? (
        <p role="status">
          {semiTask.reused
            ? "已复用现有 Semi Task / Reused existing Semi Task"
            : "已创建并发布 Semi Task / Created and published Semi Task"}{" "}
          {semiTask.task_id} · {semiTask.status}
        </p>
      ) : null}
    </section>
  );
}

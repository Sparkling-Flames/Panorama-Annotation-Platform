import { type FormEvent, useState } from "react";

import { apiFetch } from "../api";

const IMPORTER_VERSION = "panorama-layout-json-v1";

type Preview = {
  preview_id: string;
  preview_media: {
    height: number;
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

async function responseError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: { code?: string } };
    return payload.error?.code ?? "prediction_import_failed";
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function createPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null || busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    setArtifact(null);
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
          <button disabled={busy || artifact !== null} onClick={() => void publish()} type="button">
            冻结 Artifact / Freeze artifact
          </button>
        </section>
      ) : null}

      {artifact ? (
        <p role="status">
          Artifact {artifact.artifact_id} · SHA-256 {artifact.artifact_sha256}
        </p>
      ) : null}
    </section>
  );
}

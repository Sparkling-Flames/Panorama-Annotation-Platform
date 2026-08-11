import type { ReactNode } from "react";

import { evaluateDevicePreflight } from "./devicePreflight";

const COPY = {
  blocked: "此设备无法打开标注工作区 / This device cannot open the annotation workspace:",
  warning: "部分恢复或预览能力受限 / Limited recovery or preview capability:",
} as const;

const REASON = {
  abort_controller_unavailable: "AbortController",
  crypto_random_uuid_unavailable: "crypto.randomUUID",
  fetch_unavailable: "Fetch API",
  indexed_db_unavailable: "IndexedDB",
  screen_too_narrow: "minimum workspace width 1024px / 工作区最小宽度 1024px",
  unsupported_browser: "desktop Chrome or Edge / 桌面 Chrome 或 Edge",
  web_locks_unavailable: "Web Locks API",
  webgl_unavailable: "WebGL",
} as const;

function currentCapabilities() {
  return {
    hasAbortController: typeof AbortController !== "undefined",
    hasCryptoRandomUuid: typeof globalThis.crypto?.randomUUID === "function",
    hasFetch: typeof fetch === "function",
    hasIndexedDb: typeof indexedDB !== "undefined",
    hasWebGl: typeof WebGLRenderingContext !== "undefined",
    hasWebLocks: typeof navigator.locks !== "undefined",
    screenWidth: window.innerWidth,
    userAgent: navigator.userAgent,
  };
}

export function DevicePreflightGate({ children }: { children: ReactNode }) {
  const result = evaluateDevicePreflight(currentCapabilities());
  if (result.blockers.length > 0) {
    return (
      <section aria-label="device-preflight" role="alert">
        <p>{COPY.blocked}</p>
        <ul>
          {result.blockers.map((code) => (
            <li key={code}>{REASON[code]}</li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    <>
      {result.warnings.length > 0 ? (
        <aside aria-label="device-preflight-warning" role="status">
          <p>{COPY.warning}</p>
          <ul>
            {result.warnings.map((code) => (
              <li key={code}>{REASON[code]}</li>
            ))}
          </ul>
        </aside>
      ) : null}
      {children}
    </>
  );
}

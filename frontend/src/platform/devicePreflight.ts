export const MIN_WORKSPACE_WIDTH = 1024;

type DeviceCapabilities = {
  hasAbortController: boolean;
  hasCryptoRandomUuid: boolean;
  hasFetch: boolean;
  hasIndexedDb: boolean;
  hasWebGl: boolean;
  hasWebLocks: boolean;
  screenWidth: number;
  userAgent: string;
};

type PreflightCode =
  | "abort_controller_unavailable"
  | "crypto_random_uuid_unavailable"
  | "fetch_unavailable"
  | "indexed_db_unavailable"
  | "screen_too_narrow"
  | "unsupported_browser"
  | "web_locks_unavailable"
  | "webgl_unavailable";

export function evaluateDevicePreflight(capabilities: DeviceCapabilities): {
  blockers: PreflightCode[];
  warnings: PreflightCode[];
} {
  const blockers: PreflightCode[] = [];
  const warnings: PreflightCode[] = [];
  const desktopChromeOrEdge =
    /(?:Chrome|Edg)\/\d+/.test(capabilities.userAgent) &&
    !/(?:Android|iPad|iPhone|Mobi)/.test(capabilities.userAgent);
  if (!desktopChromeOrEdge) blockers.push("unsupported_browser");
  if (capabilities.screenWidth < MIN_WORKSPACE_WIDTH) blockers.push("screen_too_narrow");
  if (!capabilities.hasAbortController) blockers.push("abort_controller_unavailable");
  if (!capabilities.hasCryptoRandomUuid) blockers.push("crypto_random_uuid_unavailable");
  if (!capabilities.hasFetch) blockers.push("fetch_unavailable");
  if (!capabilities.hasWebLocks) blockers.push("web_locks_unavailable");
  if (!capabilities.hasIndexedDb) warnings.push("indexed_db_unavailable");
  if (!capabilities.hasWebGl) warnings.push("webgl_unavailable");
  return { blockers, warnings };
}

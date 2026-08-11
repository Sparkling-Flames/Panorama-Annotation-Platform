import type { ClientActivityEvent } from "../activity/activityTracker";
import type { AnnotationState } from "../annotationState";

const DATABASE_NAME = "panorama-annotation-recovery";
const DATABASE_VERSION = 1;
const DRAFT_STORE = "drafts";
const EVENT_STORE = "activity-events";

export type QueuedActivityEvent = ClientActivityEvent & { tab_id: string };

export type DraftRecoveryRecord = {
  assignment_id: string;
  base_version: number;
  draft_cycle_id: string;
  draft_state: AnnotationState;
  key: string;
  pending_patch: { op: "replace"; value: AnnotationState } | null;
  undo_stack: { future: AnnotationState[]; past: AnnotationState[] };
  updated_at: string;
};

let database: Promise<IDBDatabase> | null = null;

function openDatabase(): Promise<IDBDatabase> | null {
  if (!("indexedDB" in globalThis)) return null;
  database ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(DRAFT_STORE)) {
        request.result.createObjectStore(DRAFT_STORE, { keyPath: "key" });
      }
      if (!request.result.objectStoreNames.contains(EVENT_STORE)) {
        request.result.createObjectStore(EVENT_STORE, { keyPath: "event_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}

async function write(storeName: string, value: unknown): Promise<void> {
  const opened = openDatabase();
  if (opened === null) return;
  const db = await opened;
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(storeName, "readwrite");
    transaction.objectStore(storeName).put(value);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export function saveDraftRecovery(record: DraftRecoveryRecord): Promise<void> {
  return write(DRAFT_STORE, record);
}

export async function loadDraftRecovery(assignmentId: string): Promise<DraftRecoveryRecord | null> {
  const opened = openDatabase();
  if (opened === null) return null;
  const db = await opened;
  return new Promise((resolve, reject) => {
    const request = db.transaction(DRAFT_STORE).objectStore(DRAFT_STORE).get(assignmentId);
    request.onsuccess = () => {
      const record = request.result as DraftRecoveryRecord | undefined;
      resolve(record?.assignment_id === assignmentId ? record : null);
    };
    request.onerror = () => reject(request.error);
  });
}

export async function clearDraftRecovery(assignmentId: string): Promise<void> {
  const opened = openDatabase();
  if (opened === null) return;
  const db = await opened;
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(DRAFT_STORE, "readwrite");
    transaction.objectStore(DRAFT_STORE).delete(assignmentId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export function queueActivityEvent(event: QueuedActivityEvent): Promise<void> {
  return write(EVENT_STORE, event);
}

export async function removeQueuedActivityEvent(eventId: string): Promise<void> {
  const opened = openDatabase();
  if (opened === null) return;
  const db = await opened;
  await new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(EVENT_STORE, "readwrite");
    transaction.objectStore(EVENT_STORE).delete(eventId);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export async function queuedActivityEvents(assignmentId: string): Promise<QueuedActivityEvent[]> {
  const opened = openDatabase();
  if (opened === null) return [];
  const db = await opened;
  return new Promise((resolve, reject) => {
    const request = db.transaction(EVENT_STORE).objectStore(EVENT_STORE).getAll();
    request.onsuccess = () =>
      resolve(
        (request.result as QueuedActivityEvent[]).filter(
          (event) => event.assignment_id === assignmentId,
        ),
      );
    request.onerror = () => reject(request.error);
  });
}

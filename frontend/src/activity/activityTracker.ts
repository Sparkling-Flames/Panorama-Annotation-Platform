export const IDLE_THRESHOLD_MS = 15_000;
export const HEARTBEAT_INTERVAL_MS = 30_000;
export const ACTIVE_TIME_RULE_VERSION = "active-time-v1";

export type ActivityVisibility = "hidden" | "visible";

const ALLOWED_INTERACTION_TYPES = [
  "active_3d_check",
  "annotation_2d_edit",
  "image_zoom_pan",
  "metadata_edit",
  "pair_reorder",
  "undo_redo",
] as const;

export type AllowedInteractionType = (typeof ALLOWED_INTERACTION_TYPES)[number];

export type ActivityEventType = "focus" | "heartbeat" | "idle" | "interaction" | "visibility";

export type ActivityContext = Readonly<{
  active_time_rule_version: string;
  assignment_id: string;
  client_build_sha: string;
  client_session_id: string;
  draft_cycle_id: string;
}>;

// The server adds server_received_at when it accepts this immutable client envelope in task 8.4.
export type ClientActivityEvent = Readonly<
  ActivityContext & {
    active_lease_id: string | null;
    active_time_rule_version: string;
    client_monotonic_ms: number;
    client_wall_time_ms: number;
    event_id: string;
    event_type: ActivityEventType;
    focus: boolean;
    interaction_type: AllowedInteractionType | null;
    sequence_no: number;
    visibility: ActivityVisibility;
  }
>;

const ALLOWED_INTERACTIONS = new Set<string>(ALLOWED_INTERACTION_TYPES);

export class ActivityTracker {
  private activeLeaseId: string | null = null;
  private focus: boolean;
  private heartbeatTimer: ReturnType<typeof setTimeout> | undefined;
  private idleTimer: ReturnType<typeof setTimeout> | undefined;
  private sequence = 0;
  private stopped = false;
  private visibility: ActivityVisibility;

  constructor(
    private readonly context: ActivityContext,
    private readonly emit: (event: ClientActivityEvent) => void,
    initialState: { focus: boolean; visibility: ActivityVisibility },
  ) {
    this.focus = initialState.focus;
    this.visibility = initialState.visibility;
  }

  recordInteraction(interactionType: string): boolean {
    if (
      this.stopped ||
      !this.focus ||
      this.visibility !== "visible" ||
      !ALLOWED_INTERACTIONS.has(interactionType)
    ) {
      return false;
    }

    if (this.activeLeaseId === null) {
      this.activeLeaseId = crypto.randomUUID();
      this.scheduleHeartbeat();
    }
    this.publish("interaction", interactionType as AllowedInteractionType);
    this.scheduleIdle();
    return true;
  }

  setFocus(focus: boolean): void {
    if (this.stopped || focus === this.focus) {
      return;
    }
    this.focus = focus;
    this.publish("focus", null);
    if (!focus) {
      this.endLease();
    }
  }

  setVisibility(visibility: ActivityVisibility): void {
    if (this.stopped || visibility === this.visibility) {
      return;
    }
    this.visibility = visibility;
    this.publish("visibility", null);
    if (visibility === "hidden") {
      this.endLease();
    }
  }

  stop(): void {
    this.stopped = true;
    this.endLease();
  }

  private publish(
    eventType: ActivityEventType,
    interactionType: AllowedInteractionType | null,
  ): void {
    this.sequence += 1;
    this.emit(
      Object.freeze({
        ...this.context,
        active_lease_id: this.activeLeaseId,
        active_time_rule_version: this.context.active_time_rule_version,
        client_monotonic_ms: performance.now(),
        client_wall_time_ms: Date.now(),
        event_id: crypto.randomUUID(),
        event_type: eventType,
        focus: this.focus,
        interaction_type: interactionType,
        sequence_no: this.sequence,
        visibility: this.visibility,
      }),
    );
  }

  private scheduleIdle(): void {
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      this.publish("idle", null);
      this.endLease();
    }, IDLE_THRESHOLD_MS);
  }

  private scheduleHeartbeat(): void {
    clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => {
      if (this.activeLeaseId === null || !this.focus || this.visibility !== "visible") {
        return;
      }
      this.publish("heartbeat", null);
      this.scheduleHeartbeat();
    }, HEARTBEAT_INTERVAL_MS);
  }

  private endLease(): void {
    clearTimeout(this.heartbeatTimer);
    clearTimeout(this.idleTimer);
    this.heartbeatTimer = undefined;
    this.idleTimer = undefined;
    this.activeLeaseId = null;
  }
}

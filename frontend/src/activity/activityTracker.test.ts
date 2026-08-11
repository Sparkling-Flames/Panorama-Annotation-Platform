import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACTIVE_TIME_RULE_VERSION,
  ActivityTracker,
  type ClientActivityEvent,
} from "./activityTracker";

const context = {
  active_time_rule_version: ACTIVE_TIME_RULE_VERSION,
  assignment_id: "assignment-001",
  client_build_sha: "build-abc123",
  client_session_id: "session-001",
  draft_cycle_id: "draft-cycle-001",
};

describe("ActivityTracker", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("PAP-AOF-SC-001 keeps page-open time inactive and caps a lease after 15 seconds idle", () => {
    const events: ClientActivityEvent[] = [];
    const tracker = new ActivityTracker(context, (event) => events.push(event), {
      focus: true,
      visibility: "visible",
    });

    vi.advanceTimersByTime(60_000);
    expect(events).toEqual([]);

    expect(tracker.recordInteraction("annotation_2d_edit")).toBe(true);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      ...context,
      active_time_rule_version: ACTIVE_TIME_RULE_VERSION,
      client_wall_time_ms: expect.any(Number),
      event_type: "interaction",
      focus: true,
      interaction_type: "annotation_2d_edit",
      sequence_no: 1,
      visibility: "visible",
    });
    expect(events[0].active_lease_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(Object.isFrozen(events[0])).toBe(true);
    expect(events[0]).not.toHaveProperty("active_seconds");
    expect(events[0]).not.toHaveProperty("pointer_coordinates");
    expect(events[0]).not.toHaveProperty("key_content");
    expect(events[0]).not.toHaveProperty("server_received_at");

    vi.advanceTimersByTime(14_999);
    expect(events).toHaveLength(1);
    vi.advanceTimersByTime(1);

    expect(events).toHaveLength(2);
    expect(events[1]).toMatchObject({
      active_lease_id: events[0].active_lease_id,
      event_type: "idle",
      interaction_type: null,
      sequence_no: 2,
    });
    vi.advanceTimersByTime(60_000);
    expect(events).toHaveLength(2);
    tracker.stop();
  });

  it("task 8.1 emits 30 second heartbeats without letting them refresh the idle deadline", () => {
    const events: ClientActivityEvent[] = [];
    const tracker = new ActivityTracker(context, (event) => events.push(event), {
      focus: true,
      visibility: "visible",
    });

    tracker.recordInteraction("annotation_2d_edit");
    vi.advanceTimersByTime(10_000);
    tracker.recordInteraction("pair_reorder");
    vi.advanceTimersByTime(10_000);
    tracker.recordInteraction("undo_redo");
    vi.advanceTimersByTime(10_000);

    expect(events.map((event) => event.event_type)).toEqual([
      "interaction",
      "interaction",
      "interaction",
      "heartbeat",
    ]);
    expect(events.map((event) => event.sequence_no)).toEqual([1, 2, 3, 4]);
    expect(new Set(events.map((event) => event.active_lease_id))).toEqual(
      new Set([events[0].active_lease_id]),
    );

    vi.advanceTimersByTime(5_000);
    expect(events.at(-1)?.event_type).toBe("idle");
    vi.advanceTimersByTime(30_000);
    expect(events.filter((event) => event.event_type === "heartbeat")).toHaveLength(1);
    tracker.stop();
  });

  it("PAP-AOF-SC-002 emits focus and visibility changes immediately and requires a new interaction", () => {
    const events: ClientActivityEvent[] = [];
    const tracker = new ActivityTracker(context, (event) => events.push(event), {
      focus: true,
      visibility: "visible",
    });

    tracker.recordInteraction("metadata_edit");
    const firstLease = events[0].active_lease_id;
    tracker.setFocus(false);
    expect(events.at(-1)).toMatchObject({
      active_lease_id: firstLease,
      event_type: "focus",
      focus: false,
    });

    tracker.setFocus(true);
    expect(events.at(-1)).toMatchObject({
      active_lease_id: null,
      event_type: "focus",
      focus: true,
    });
    vi.advanceTimersByTime(60_000);
    expect(events.at(-1)?.event_type).toBe("focus");

    tracker.recordInteraction("image_zoom_pan");
    const secondLease = events.at(-1)?.active_lease_id;
    expect(secondLease).not.toBe(firstLease);
    tracker.setVisibility("hidden");
    expect(events.at(-1)).toMatchObject({
      active_lease_id: secondLease,
      event_type: "visibility",
      visibility: "hidden",
    });
    expect(tracker.recordInteraction("active_3d_check")).toBe(false);

    tracker.setVisibility("visible");
    expect(events.at(-1)).toMatchObject({
      active_lease_id: null,
      event_type: "visibility",
      visibility: "visible",
    });
    tracker.recordInteraction("active_3d_check");
    expect(events.at(-1)).toMatchObject({
      draft_cycle_id: context.draft_cycle_id,
      event_type: "interaction",
      interaction_type: "active_3d_check",
    });
    expect(events.at(-1)?.active_lease_id).not.toBe(secondLease);
    tracker.stop();
  });

  it("PAP-AOF-SC-005 accepts only the specified coarse interactions", () => {
    const events: ClientActivityEvent[] = [];
    const tracker = new ActivityTracker(context, (event) => events.push(event), {
      focus: true,
      visibility: "visible",
    });

    for (const interaction of [
      "annotation_2d_edit",
      "pair_reorder",
      "undo_redo",
      "metadata_edit",
      "image_zoom_pan",
      "active_3d_check",
    ]) {
      expect(tracker.recordInteraction(interaction)).toBe(true);
    }
    for (const ignored of ["pointer_move", "background_heartbeat", "automatic_3d_rebuild"]) {
      expect(tracker.recordInteraction(ignored)).toBe(false);
    }

    expect(events.map((event) => event.interaction_type)).toEqual([
      "annotation_2d_edit",
      "pair_reorder",
      "undo_redo",
      "metadata_edit",
      "image_zoom_pan",
      "active_3d_check",
    ]);
    tracker.stop();
  });
});

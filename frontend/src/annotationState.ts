export type AnnotationPoint = {
  point_id: string;
  u: number;
  v: number;
};

export type AnnotationPointPair = {
  pair_id: string;
  order_index: number;
  top: AnnotationPoint;
  bottom: AnnotationPoint;
};

export type AnnotationState = {
  pairs: AnnotationPointPair[];
  seam_anchor_pair_id: string;
};

export function canonicalAnnotationJson(state: AnnotationState): string {
  const pairs = [...state.pairs]
    .sort((left, right) => left.order_index - right.order_index)
    .map((pair) => ({
      bottom: {
        point_id: pair.bottom.point_id,
        u: pair.bottom.u,
        v: pair.bottom.v,
      },
      order_index: pair.order_index,
      pair_id: pair.pair_id,
      top: {
        point_id: pair.top.point_id,
        u: pair.top.u,
        v: pair.top.v,
      },
    }));
  return JSON.stringify({ pairs, seam_anchor_pair_id: state.seam_anchor_pair_id });
}

export async function annotationStateSha(state: AnnotationState): Promise<string> {
  const input = new TextEncoder().encode(canonicalAnnotationJson(state));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

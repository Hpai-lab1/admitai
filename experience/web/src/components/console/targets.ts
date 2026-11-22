/** A PRD target (3.1, 7.5): whether a rate meets it, said in words as well as colour. */
export type Target = { min?: number; max?: number; text: string };

export function meets(v: number | null | undefined, t: Target): boolean | null {
  if (v == null) return null;
  return (t.min == null || v >= t.min) && (t.max == null || v <= t.max);
}

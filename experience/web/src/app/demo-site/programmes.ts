import { API_URL } from "@/lib/config";

/** A programme as the college's website lists it. The list comes from the knowledge base, which
 * builds itself from the college's website (Module A site_kb.py), through Module C's public
 * GET /widget/programmes: nothing here is written by hand. */
export type Programme = {
  code: string;
  title: string;
  level: "UG" | "PG" | "PhD" | "Diploma";
  faculty: string;
  campus?: string;
  duration: string;
  intake: string;
  fees: string[];
  /** The page on the college's website this programme was read from. */
  url: string;
  /** The website states eligibility / career paths for it (so those questions can be answered). */
  eligibility: boolean;
  careers: boolean;
};

export async function loadProgrammes(publicKey: string): Promise<Programme[]> {
  const res = await fetch(`${API_URL}/widget/programmes?public_key=${encodeURIComponent(publicKey)}`);
  if (!res.ok) throw new Error(`The programme list could not be loaded (${res.status}).`);
  return ((await res.json()) as { items: Programme[] }).items;
}

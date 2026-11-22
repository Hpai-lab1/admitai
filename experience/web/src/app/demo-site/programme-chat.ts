import type { Focus } from "@/components/widget/Widget";
import type { Programme } from "./programmes";

/** Up to four one-tap questions, only about what the website states for this programme. */
export function suggestionsFor(p: Programme): string[] {
  const t = p.title;
  const out: string[] = [];
  if (p.fees.length) out.push(`What is the fee for ${t}?`);
  if (p.eligibility) out.push(`What is the eligibility for ${t}?`);
  if (p.careers) out.push(`What careers can ${t} lead to?`);
  if (p.intake) out.push(`How many seats are there in ${t}?`);
  if (p.duration) out.push(`How long is ${t}?`);
  return out.slice(0, 4);
}

/** What the programme's chat opens with. */
export function focusFor(p: Programme): Focus {
  return {
    title: p.title,
    greeting: `I see you're interested in ${p.title}. What would you like to know? Pick a question below or type your own.`,
    suggestions: suggestionsFor(p),
    label: "Popular questions",
  };
}

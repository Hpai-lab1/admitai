/** The widget's thumbs-down flow: which step comes after a rating.
 *  "down" asks what was wrong; "other" asks for the text (required); "detail" offers an optional
 *  box after "wrong", "outdated" or "incomplete", so the visitor can say what the right
 *  information is (Module A checks it against the college's own pages); "sent" is done. */
export const REASONS = [
  "wrong",
  "outdated",
  "incomplete",
  "not_what_i_asked",
  "hard_to_understand",
  "other",
] as const;
export type Reason = (typeof REASONS)[number];
export const DETAIL_REASONS: readonly Reason[] = ["wrong", "outdated", "incomplete"];
export type FeedbackStep = "down" | "other" | "detail" | "sent";

export function nextFeedback(value: 1 | -1, reason?: Reason, comment?: string): FeedbackStep {
  if (value === 1 || comment !== undefined) return "sent";
  if (!reason) return "down";
  if (reason === "other") return "other";
  return DETAIL_REASONS.includes(reason) ? "detail" : "sent";
}

/** After the chat's stars: "what best describes this chat?" (19 Nov 2026). Each choice maps to a
 *  reason the feedback API accepts (contracts/experience.openapi), so Module A's learning sees it:
 *  "satisfied" sends no reason; "not_satisfied" and "other" send "other" with the visitor's words. */
export const CHAT_CHOICES = [
  "satisfied",
  "not_satisfied",
  "wrong",
  "outdated",
  "incomplete",
  "not_what_i_asked",
  "hard_to_understand",
  "other",
] as const;
export type ChatChoice = (typeof CHAT_CHOICES)[number];

/** The choice preselected for a number of stars: "satisfied" for 4 or 5, none below. */
export function defaultChoice(stars: number): ChatChoice | "" {
  return stars >= 4 ? "satisfied" : "";
}

/** reason and comment for the rating, or null while it cannot be sent ("other" needs words). */
export function chatRatingFeedback(
  choice: ChatChoice | "",
  text: string,
): { reason: Reason | null; comment: string | null } | null {
  const words = text.trim().slice(0, 1000) || null;
  if (!choice) return { reason: null, comment: words };
  if (choice === "satisfied") return { reason: null, comment: words };
  if (choice === "other") return words ? { reason: "other", comment: words } : null;
  if (choice === "not_satisfied")
    return { reason: "other", comment: words ? `Not satisfactory: ${words}` : "Not satisfactory" };
  return { reason: choice, comment: words };
}

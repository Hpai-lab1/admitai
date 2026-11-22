export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8200";
export const ASSISTANT_URL = process.env.NEXT_PUBLIC_ASSISTANT_URL ?? "http://127.0.0.1:8230/b";
/** The college's AI phone line (Vapi), shown next to "Admissions enquiries" on the demo site.
 * Set in .env.local only: phone numbers stay out of the repository. Empty: nothing is shown. */
export const ADMISSIONS_PHONE = process.env.NEXT_PUBLIC_ADMISSIONS_PHONE ?? "";

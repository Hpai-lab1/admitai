export type LeadSummary = {
  id: string;
  name: string | null;
  phone: string | null;
  email: string | null;
  city: string | null;
  is_minor: boolean;
  guardian_contact: { channel: string; destination: string } | null;
  program_interest: string[];
  band: "hot" | "warm" | "cold";
  score: number;
  stage: string;
};

export type Handoff = {
  id: string;
  conversation_id: string;
  queue: "admission" | "faculty";
  department: string | null;
  kind: "handoff" | "callback";
  reason: string;
  status: "open" | "claimed" | "resolved";
  language: string;
  context: {
    transcript?: { role: string; text: string }[];
    ai_draft?: string | null;
    sources?: { title: string; academic_year?: string }[];
    confidence?: number | null;
  };
  preferred_time: string | null;
  preferred_channel: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  sla_due_at: string;
  overdue: boolean;
  created_at: string;
  lead: LeadSummary | null;
  events: { kind: string; body: string | null; at: string; actor: string | null }[];
  feedback: {
    kind: "thumb" | "rating";
    value: number;
    reason: string | null;
    comment: string | null;
    created_at: string;
  }[];
};

export type Lead = LeadSummary & {
  qualification: string | null;
  source: { channel?: string; utm?: Record<string, string> };
  owner_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  anonymised: boolean;
};

/** Where a staff reply can be e-mailed (PRD F3.6): the applicant's own address, or for a minor
 *  the guardian's verified e-mail. Mirrors Module C's `messaging.applicant_email`. */
export function replyEmail(lead: LeadSummary | null): string | null {
  if (!lead) return null;
  if (lead.is_minor)
    return lead.guardian_contact?.channel === "email" ? lead.guardian_contact.destination : null;
  return lead.email;
}

/** Where a staff reply can go on WhatsApp (PRD F3.6): the adult applicant's own number, or the
 *  guardian's WhatsApp number when a minor's guardian verified on WhatsApp. Mirrors Module C's
 *  `messaging.staff_reply_whatsapp`; Module B still refuses anyone who opted out. */
export function replyWhatsapp(lead: LeadSummary | null): string | null {
  if (!lead) return null;
  if (lead.is_minor)
    return lead.guardian_contact?.channel === "whatsapp" ? lead.guardian_contact.destination : null;
  return lead.phone;
}

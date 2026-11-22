import { STRINGS, type Lang } from "./i18n";
import type { TenantInfo } from "./widget-api";

export type LocalTenant = {
  assistant_name: string;
  greeting: string;
  announcement: string | null;
  starter_questions: string[];
  topics: string[];
};

const DEFAULT_NAME = "Admissions Assistant";

/** The college's own widget texts in the visitor's language. In Hindi or Kannada nothing is
 *  shown in English: a text the college has not translated is left out, except the name and
 *  greeting, which fall back to the widget's own wording (a brand name is kept as it is). */
export function localTenant(tenant: TenantInfo, lang: Lang): LocalTenant {
  if (lang === "en")
    return {
      assistant_name: tenant.assistant_name,
      greeting: tenant.greeting,
      announcement: tenant.announcement,
      starter_questions: tenant.starter_questions,
      topics: tenant.topics,
    };
  const tr = tenant.translations?.[lang] ?? {};
  const t = STRINGS[lang];
  return {
    assistant_name:
      tr.assistant_name ??
      (tenant.assistant_name === DEFAULT_NAME ? t.assistantTitle : tenant.assistant_name),
    greeting: tr.greeting ?? t.greeting,
    announcement: tr.announcement ?? null,
    starter_questions: tr.starter_questions ?? [],
    topics: tr.topics ?? [],
  };
}

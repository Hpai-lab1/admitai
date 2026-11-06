// Widget text in English and Hindi (PRD F1.6, Level 1). Answers themselves come from Module B in the
// chosen language; this file covers the widget's own buttons, labels and consent wording.
export type Lang = "en" | "hi";

const en = {
  placeholder: "Type your question…",
  ask: "Ask",
  enquire: "Enquire now",
  talk: "Talk to a person",
  apply: "Apply now",
  withdraw: "Withdraw consent",
  rate: "Rate this chat",
  admissions: "Admissions",
  source: "Sources",
  askSomeone: "Yes, ask someone to contact me",
  leadAsk: "Would you like our admissions team to follow up with you? You choose what to share.",
  leadYes: "Share my details",
  helpful: "Helpful",
  notHelpful: "Not helpful",
  wasHelpful: "Was this helpful?",
  whatWrong: "What was wrong?",
  tellUs: "Tell us more (optional)",
  send: "Send",
  thanks: "Thanks for the feedback.",
  rateQ: "How was this chat?",
  rateThanks: "Thank you for rating this chat.",
  reconnecting: "Connection lost. Reconnecting…",
  lostAnswer: "The connection was lost before the answer arrived. Please ask again.",
  showMore: "Show more",
  showLess: "Show less",
  moreQuestions: "You could also ask",
  stars: (n: number) => `${n} out of 5`,
  close: "Close chat",
  textSize: "Text size",
  smaller: "Smaller text",
  normal: "Normal text",
  larger: "Larger text",
  speak: "Speak your question",
  stopSpeak: "Stop voice input",
  listening: "Listening… press again when you have finished",
  thinking: "Thinking…",
  speaking: "Speaking… press the microphone to interrupt",
  micDenied: "The microphone is blocked. Allow it in your browser's site settings, or type your question.",
  you: "You said: ",
  assistant: "Assistant: ",
  loading: "Loading…",
  withdrawn: "Your consent is withdrawn and any contact details you shared have been deleted.",
  erase: "Delete my data",
  eraseConfirm: "Tap again to delete this chat and your contact details",
  erased:
    "Your conversation and contact details are being deleted from our systems. Anonymous counts (for example, that a question was asked) are kept.",
  failed: "Sorry, that didn't work. Please try again.",
  noVoice: "Voice input isn't supported in this browser. Please type your question.",
  language: "Language",
  reasons: {
    wrong: "Wrong",
    outdated: "Out of date",
    incomplete: "Incomplete",
    not_what_i_asked: "Not what I asked",
    hard_to_understand: "Hard to understand",
    other: "Other",
  },
  // contact flow
  talkTitle: "Talk to a person",
  enquireTitle: "Enquire now",
  allSet: "All set",
  backToChat: "← Chat",
  consentText: (college: string) =>
    `May ${college} contact you about admissions? We use your details only for this, keep them safe, and you can withdraw at any time from this chat.`,
  consentContact: "Yes, the admissions team may contact me about my enquiry. (required)",
  consentMarketing: "Also send me news about programmes and events. (optional)",
  privacy: "Read the privacy notice",
  agree: "Agree and continue",
  notNow: "Not now",
  ageQ: "Are you 18 years or older?",
  adult: "Yes, I am 18 or older",
  minor: "No, I am under 18",
  name: "Your name",
  phone: "Mobile number with country code",
  phoneHint: "For example +91 followed by 10 digits",
  email: "E-mail",
  interested: "Interested in",
  notSure: "Not sure yet",
  reachBy: "How should we reach you?",
  byPhone: "Phone call",
  byEmail: "E-mail",
  byWhatsapp: "WhatsApp",
  bestTime: "Best time to contact you",
  bestTimeEg: "e.g. weekday evenings",
  requestCallback: "Request a call back",
  sendEnquiry: "Send enquiry",
  needContact: "Please share a phone number or an e-mail address.",
  whatsappNeedsPhone: "WhatsApp needs a phone number.",
  guardianIntro:
    "Because you are under 18, we need a parent or guardian to agree. We'll send them a code, and we won't keep your own phone number or e-mail.",
  sendCodeBy: "Send the code by",
  guardianEmail: "Parent's or guardian's e-mail",
  guardianWhatsapp: "Parent's WhatsApp number",
  sendCode: "Send code to parent",
  askCode: "Ask them for the code and type it here.",
  waGuardianHint:
    "Your parent or guardian should send any message to the college on WhatsApp from that number; the code comes back in reply.",
  devCode: "Development mode: the code is",
  code: "6-digit code",
  confirm: "Confirm",
  guardianOk: "Thank you, your parent or guardian has agreed.",
  firstName: "Your first name",
  parentTime: "Best time to contact your parent",
  enquiryDone: (h: number) => `Thank you. The admissions team will be in touch within ${h} hours.`,
};

export type Strings = typeof en;

const hi: Strings = {
  placeholder: "अपना प्रश्न लिखें…",
  ask: "पूछें",
  enquire: "पूछताछ करें",
  talk: "किसी व्यक्ति से बात करें",
  apply: "अभी आवेदन करें",
  withdraw: "सहमति वापस लें",
  rate: "इस चैट को रेट करें",
  admissions: "प्रवेश",
  source: "स्रोत",
  askSomeone: "हाँ, किसी से मुझसे संपर्क करवाएँ",
  leadAsk: "क्या हमारी प्रवेश टीम आपसे संपर्क करे? आप तय करें कि क्या साझा करना है।",
  leadYes: "मेरी जानकारी साझा करें",
  helpful: "उपयोगी",
  notHelpful: "उपयोगी नहीं",
  wasHelpful: "क्या यह उपयोगी था?",
  whatWrong: "क्या गलत था?",
  tellUs: "और बताएँ (वैकल्पिक)",
  send: "भेजें",
  thanks: "प्रतिक्रिया के लिए धन्यवाद।",
  rateQ: "यह चैट कैसी रही?",
  rateThanks: "रेटिंग के लिए धन्यवाद।",
  reconnecting: "कनेक्शन टूट गया। फिर से जोड़ रहे हैं…",
  lostAnswer: "जवाब आने से पहले कनेक्शन टूट गया। कृपया फिर से पूछें।",
  showMore: "और दिखाएँ",
  showLess: "कम दिखाएँ",
  moreQuestions: "आप यह भी पूछ सकते हैं",
  stars: (n: number) => `5 में से ${n}`,
  close: "चैट बंद करें",
  textSize: "अक्षर का आकार",
  smaller: "छोटे अक्षर",
  normal: "सामान्य अक्षर",
  larger: "बड़े अक्षर",
  speak: "अपना प्रश्न बोलें",
  stopSpeak: "बोलना बंद करें",
  listening: "सुन रहा है… बोलने के बाद फिर दबाएँ",
  thinking: "सोच रहा है…",
  speaking: "बोल रहा है… रोकने के लिए माइक दबाएँ",
  micDenied: "माइक्रोफ़ोन बंद है। ब्राउज़र की साइट सेटिंग में अनुमति दें, या प्रश्न लिखें।",
  you: "आपने कहा: ",
  assistant: "सहायक: ",
  loading: "लोड हो रहा है…",
  withdrawn: "आपकी सहमति वापस ले ली गई है और आपके दिए संपर्क विवरण हटा दिए गए हैं।",
  erase: "मेरा डेटा हटाएँ",
  eraseConfirm: "यह चैट और आपके संपर्क विवरण हटाने के लिए फिर से टैप करें",
  erased:
    "आपकी बातचीत और संपर्क विवरण हमारे सिस्टम से हटाए जा रहे हैं। केवल गुमनाम गिनती (जैसे कोई प्रश्न पूछा गया) रखी जाती है।",
  failed: "क्षमा करें, यह नहीं हो पाया। कृपया फिर से कोशिश करें।",
  noVoice: "इस ब्राउज़र में आवाज़ से इनपुट उपलब्ध नहीं है। कृपया प्रश्न लिखें।",
  language: "भाषा",
  reasons: {
    wrong: "गलत",
    outdated: "पुराना",
    incomplete: "अधूरा",
    not_what_i_asked: "मैंने यह नहीं पूछा",
    hard_to_understand: "समझना कठिन",
    other: "अन्य",
  },
  talkTitle: "किसी व्यक्ति से बात करें",
  enquireTitle: "पूछताछ करें",
  allSet: "हो गया",
  backToChat: "← चैट",
  consentText: (college: string) =>
    `क्या ${college} प्रवेश के बारे में आपसे संपर्क कर सकता है? हम आपकी जानकारी केवल इसी के लिए उपयोग करते हैं, उसे सुरक्षित रखते हैं, और आप इस चैट से कभी भी सहमति वापस ले सकते हैं।`,
  consentContact: "हाँ, प्रवेश टीम मेरी पूछताछ के बारे में मुझसे संपर्क कर सकती है। (आवश्यक)",
  consentMarketing: "मुझे कार्यक्रमों और आयोजनों की खबरें भी भेजें। (वैकल्पिक)",
  privacy: "गोपनीयता सूचना पढ़ें",
  agree: "सहमत हूँ, आगे बढ़ें",
  notNow: "अभी नहीं",
  ageQ: "क्या आपकी उम्र 18 वर्ष या उससे अधिक है?",
  adult: "हाँ, मैं 18 या उससे अधिक का/की हूँ",
  minor: "नहीं, मैं 18 से कम का/की हूँ",
  name: "आपका नाम",
  phone: "देश कोड सहित मोबाइल नंबर",
  phoneHint: "उदाहरण: +91 और फिर 10 अंक",
  email: "ई-मेल",
  interested: "किसमें रुचि है",
  notSure: "अभी तय नहीं",
  reachBy: "हम आपसे कैसे संपर्क करें?",
  byPhone: "फ़ोन कॉल",
  byEmail: "ई-मेल",
  byWhatsapp: "व्हाट्सऐप",
  bestTime: "संपर्क का सबसे अच्छा समय",
  bestTimeEg: "जैसे सप्ताह के दिनों में शाम",
  requestCallback: "कॉल बैक का अनुरोध करें",
  sendEnquiry: "पूछताछ भेजें",
  needContact: "कृपया फ़ोन नंबर या ई-मेल पता दें।",
  whatsappNeedsPhone: "व्हाट्सऐप के लिए फ़ोन नंबर चाहिए।",
  guardianIntro:
    "क्योंकि आपकी उम्र 18 से कम है, माता-पिता या अभिभावक की सहमति ज़रूरी है। हम उन्हें एक कोड भेजेंगे, और आपका अपना फ़ोन नंबर या ई-मेल नहीं रखेंगे।",
  sendCodeBy: "कोड भेजें",
  guardianEmail: "माता-पिता या अभिभावक का ई-मेल",
  guardianWhatsapp: "माता-पिता का व्हाट्सऐप नंबर",
  sendCode: "माता-पिता को कोड भेजें",
  askCode: "उनसे कोड पूछें और यहाँ लिखें।",
  waGuardianHint:
    "आपके माता-पिता या अभिभावक उसी नंबर से कॉलेज को व्हाट्सऐप पर कोई भी संदेश भेजें; जवाब में उन्हें कोड मिलेगा।",
  devCode: "डेवलपमेंट मोड: कोड है",
  code: "6 अंकों का कोड",
  confirm: "पुष्टि करें",
  guardianOk: "धन्यवाद, आपके माता-पिता या अभिभावक ने सहमति दे दी है।",
  firstName: "आपका पहला नाम",
  parentTime: "माता-पिता से संपर्क का सबसे अच्छा समय",
  enquiryDone: (h: number) => `धन्यवाद। प्रवेश टीम ${h} घंटों के भीतर संपर्क करेगी।`,
};

export const STRINGS: Record<Lang, Strings> = { en, hi };

/** Language of a piece of text, for the lang attribute and auto-switching (Devanagari -> Hindi). */
export function detectLang(text: string): Lang | "kn" | null {
  if (/[ऀ-ॿ]/.test(text)) return "hi";
  if (/[ಀ-೿]/.test(text)) return "kn";
  if (/[a-z]/i.test(text)) return "en";
  return null;
}

/** Words of a question, for spotting a rephrased repeat (PRD F8.3). */
function words(s: string): Set<string> {
  return new Set(
    s
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .split(/\s+/)
      .filter((w) => w.length > 2),
  );
}

/** True when the new question mostly repeats the previous one (Jaccard overlap >= 0.5). */
export function isRephrase(previous: string, next: string): boolean {
  const a = words(previous);
  const b = words(next);
  if (!a.size || !b.size) return false;
  let shared = 0;
  a.forEach((w) => {
    if (b.has(w)) shared++;
  });
  return shared / (a.size + b.size - shared) >= 0.5;
}

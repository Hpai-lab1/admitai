"""Answer agent prompts and role packs (PRD 7.1-7.3). In Level 1 the Admission Officer and Faculty
agents are role packs loaded into one Answer agent; a tenant may override persona and tone, never
the safety rules. Every prompt has a version so feedback can be attributed (PRD 11.2)."""

from __future__ import annotations

PROMPT_VERSION = (
    "answer-v5"  # v5: no eligibility verdicts about the visitor (roadmap item 1)
)
AGENT_VERSION = "answer-agent/0.2.0"  # name/version (no "@": not an address)

NO_ANSWER = "NO_ANSWER"

ROLE_PACKS = {
    "admission": {
        "name": "Admission Officer",
        "focus": "admission process, eligibility, fees, dates, documents, hostel, scholarships, loans and transport",
    },
    "faculty": {
        "name": "Faculty",
        "focus": "syllabus, curriculum, subjects, labs, electives, teaching, projects, careers and higher studies",
    },
}

# Wording chosen by measurement (RUAS set, qwen2.5:3b): telling a small model up front to "reply
# NO_ANSWER if the sources do not answer" made it refuse 21 of 25 answerable questions; putting the
# refusal last, as a rare case, answered 20 of 25 and still refused 18 of 18 out-of-scope questions.
SAFETY_RULES = """Rules:
1. Answer the question using the numbered sources below. Most questions are answered by source [1] or [2].
2. After every sentence that states a fact, add its source number in square brackets, like [1].
3. Copy every number, amount, date and percentage exactly as written in the source. Never calculate, round or convert.
4. Never promise admission, a seat, a scholarship or a placement. Never compare with other named colleges.
   Never tell the visitor whether they themselves are eligible or can apply, even from their age, class or marks:
   state the published criteria with their source and offer the admissions team to check their case.
5. At most 4 short sentences per question part. No tables.
6. If the question has several parts, answer each part in its own short paragraph; for a part the sources do not cover, say you are not sure about that part.
7. Only if no source is about the question at all, reply with the single word NO_ANSWER."""

LANGUAGE_LINE = {
    "en": "Answer in English.",
    "hi": "Answer in simple Hindi (Devanagari script). Keep numbers, program names and source markers exactly as in the sources.",
    "kn": "Answer in simple Kannada (Kannada script). Keep numbers, program names and source markers exactly as in the sources.",
}


# Shown after an English answer to a question asked in another language. The Kannada text was
# written without a native speaker and needs review before real use.
ENGLISH_ONLY_NOTE = {
    "hi": "(यह उत्तर अंग्रेज़ी में है; हिंदी में उत्तर जल्द उपलब्ध होंगे।)",
    "kn": "(ಈ ಉತ್ತರ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿದೆ; ಕನ್ನಡ ಉತ್ತರಗಳು ಶೀಘ್ರದಲ್ಲೇ ಲಭ್ಯವಾಗುತ್ತವೆ.)",
}


VOICE_STYLE = (
    "This answer will be spoken aloud: at most 3 short sentences, no lists or tables. If the details "
    "are long, give the key fact and offer to send the details on chat."
)


def system_prompt(
    college: str,
    assistant_name: str,
    tone: str,
    roles: list[str],
    language: str,
    voice: bool = False,
) -> str:
    packs = [ROLE_PACKS[r] for r in roles if r in ROLE_PACKS] or [
        ROLE_PACKS["admission"]
    ]
    who = " and ".join(p["name"] for p in packs)
    focus = "; ".join(p["focus"] for p in packs)
    return (
        f"You are {assistant_name}, the admissions assistant of {college}, answering as the {who} "
        f"desk ({focus}). Tone: {tone}.\n\n{SAFETY_RULES}\n\n{LANGUAGE_LINE.get(language, LANGUAGE_LINE['en'])}"
        + (f"\n\n{VOICE_STYLE}" if voice else "")
    )


def user_prompt(
    question: str,
    sources_block: str,
    history: list[dict],
    language: str = "en",
    summary: str | None = None,
) -> str:
    convo = f"Summary of the earlier conversation: {summary}\n\n" if summary else ""
    if history:
        lines = [
            f"{'Visitor' if h['sender'] == 'user' else 'Assistant'}: {h['content']}"
            for h in history
        ]
        convo += "Conversation so far:\n" + "\n".join(lines) + "\n\n"
    return (
        f"{convo}Sources:\n{sources_block}\n\nVisitor's question: {question}\n\n"
        f"Answer from the sources, with the source number in square brackets after each fact, like [1]. {LANGUAGE_LINE.get(language, LANGUAGE_LINE['en'])}"
    )


MESSAGES = {
    "en": {
        "no_answer": "I'm sorry, I don't have reliable information about that. I can ask someone from the "
        "admissions team to get back to you. Would you like that?",
        "not_certain": "I'm not certain about that detail, so I'd rather not guess. Would you like someone "
        "from the admissions team to confirm it for you?",
        "sensitive": "That's something our admissions team should answer personally. I can arrange a call back "
        "from them. Would you like that?",
        "human": "Of course. I can arrange for someone from our team to contact you. Please share how and when "
        "to reach you.",
        "injection": "I can only help with questions about admissions and studying at this college.",
        "abuse": "I'm here to help with admission questions. Let's keep the conversation respectful.",
        "rate_limited": "You're sending messages very quickly. Please wait a moment.",
        "unavailable": "I'm having trouble right now. Leave your number and our team will call you.",
        "empty": "Please type your question.",
        "too_long": "That message is too long. Please ask one question at a time.",
        "closed": "Our admissions team works {hours}. It is outside those hours now, so they will "
        "get back to you from {when}.",
        "not_translated": "I'm sorry, I couldn't give you a reliable answer in your language. Would "
        "you like someone from the admissions team to contact you?",
        "language_limited": "In your language I can answer fees, course length and admission dates "
        "for now. For this question our admissions team will help you. Shall they contact you?",
        "did_you_mean": "Did you mean {options}? Say yes, or ask your question another way.",
        "did_you_mean_pick": "Did you mean one of these? {options} Say the number, or ask your "
        "question another way.",
        "offer_declined": "No problem. What else would you like to know?",
        "did_you_mean_no": "No problem. Please ask your question another way, or tell me the "
        "programme name again.",
        "general_label": "{college}'s official information doesn't cover this, so here is a general "
        "answer. Please confirm it with the admissions team.",
        "general_offer": "For details specific to {college}, I can ask someone from the admissions "
        "team to get back to you. Would you like that?",
        # Asked for in the conversation (language.py): the reply's language, or text / voice.
        "language_set": "Sure, I'll reply in English from now on. What would you like to know?",
        # A channel that speaks one language only (Vapi phone calls: English).
        "language_only": "I'm sorry, on this line I can only speak English. What would you like "
        "to know?",
        "output_text": "Okay, I'll reply in text only.",
        "output_voice": "Okay, I'll reply with voice.",
        "output_both": "Okay, I'll reply with both text and voice.",
    },
    "hi": {
        "no_answer": "माफ़ कीजिए, इसके बारे में मेरे पास भरोसेमंद जानकारी नहीं है। क्या मैं प्रवेश टीम से आपसे संपर्क करवाऊँ?",
        "not_certain": "मुझे इस जानकारी के बारे में पक्का पता नहीं है, इसलिए मैं अनुमान नहीं लगाऊँगा। क्या प्रवेश टीम आपसे संपर्क करे?",
        "sensitive": "इस विषय पर हमारी प्रवेश टीम ही सही जानकारी देगी। क्या मैं उनसे आपको कॉल करवाऊँ?",
        "human": "ज़रूर। हमारी टीम आपसे संपर्क करेगी। कृपया बताइए कि आपसे कैसे और कब संपर्क करें।",
        "injection": "मैं केवल इस कॉलेज में प्रवेश और पढ़ाई से जुड़े प्रश्नों में मदद कर सकता हूँ।",
        "abuse": "मैं प्रवेश से जुड़े प्रश्नों में मदद के लिए हूँ। कृपया सम्मान से बात करें।",
        "rate_limited": "आप बहुत जल्दी संदेश भेज रहे हैं। कृपया थोड़ा रुकें।",
        "unavailable": "अभी कुछ दिक्कत है। अपना नंबर दीजिए, हमारी टीम आपको कॉल करेगी।",
        "empty": "कृपया अपना प्रश्न लिखें।",
        "too_long": "संदेश बहुत लंबा है। कृपया एक बार में एक प्रश्न पूछें।",
        "closed": "हमारी प्रवेश टीम {hours} उपलब्ध रहती है। अभी कार्य-समय नहीं है, इसलिए टीम {when} से "
        "आपसे संपर्क करेगी।",
        "not_translated": "माफ़ कीजिए, यह उत्तर हिंदी में भरोसेमंद रूप से नहीं दे सका। क्या प्रवेश टीम आपसे "
        "संपर्क करे?",
        "language_limited": "अभी मैं हिंदी में फीस, कोर्स की अवधि और प्रवेश की तिथियों के बारे में उत्तर दे सकता हूँ। "
        "इस प्रश्न में हमारी प्रवेश टीम हिंदी में मदद करेगी। क्या वे आपसे संपर्क करें?",
        "did_you_mean": "क्या आपका मतलब {options} था? हाँ कहिए, या प्रश्न दूसरे तरीके से पूछिए।",
        "did_you_mean_pick": "क्या आपका मतलब इनमें से कोई था? {options} नंबर बताइए, या प्रश्न दूसरे "
        "तरीके से पूछिए।",
        "offer_declined": "ठीक है। आप और क्या जानना चाहेंगे?",
        "did_you_mean_no": "कोई बात नहीं। कृपया प्रोग्राम का नाम फिर से बताइए, या प्रश्न दूसरे तरीके से पूछिए।",
        "general_label": "{college} की आधिकारिक जानकारी में यह नहीं है, इसलिए यह एक सामान्य उत्तर है। कृपया "
        "प्रवेश टीम से इसकी पुष्टि करें।",
        "general_offer": "{college} से जुड़ी पक्की जानकारी के लिए, क्या मैं प्रवेश टीम से आपसे संपर्क करवाऊँ?",
        "language_set": "ठीक है, अब से मैं हिंदी में उत्तर दूँगा। आप क्या जानना चाहेंगे?",
        "output_text": "ठीक है, मैं केवल लिखकर उत्तर दूँगा।",
        "output_voice": "ठीक है, मैं आवाज़ में उत्तर दूँगा।",
        "output_both": "ठीक है, मैं लिखकर और आवाज़ में, दोनों तरह से उत्तर दूँगा।",
    },
    # Kannada written without a native speaker: have it reviewed before real use.
    "kn": {
        "no_answer": "ಕ್ಷಮಿಸಿ, ಇದರ ಬಗ್ಗೆ ನನ್ನ ಬಳಿ ವಿಶ್ವಾಸಾರ್ಹ ಮಾಹಿತಿ ಇಲ್ಲ. ಪ್ರವೇಶ ತಂಡದವರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುವಂತೆ ಮಾಡಲೆ?",
        "not_certain": "ಈ ವಿವರದ ಬಗ್ಗೆ ನನಗೆ ಖಚಿತವಿಲ್ಲ, ಆದ್ದರಿಂದ ಊಹಿಸುವುದಿಲ್ಲ. ಪ್ರವೇಶ ತಂಡದವರು ಇದನ್ನು ನಿಮಗೆ ದೃಢಪಡಿಸಲೆ?",
        "sensitive": "ಈ ವಿಷಯಕ್ಕೆ ನಮ್ಮ ಪ್ರವೇಶ ತಂಡದವರೇ ನೇರವಾಗಿ ಉತ್ತರಿಸಬೇಕು. ಅವರಿಂದ ನಿಮಗೆ ಮರಳಿ ಕರೆ ಮಾಡಿಸಲೆ?",
        "human": "ಖಂಡಿತ. ನಮ್ಮ ತಂಡದವರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುವಂತೆ ಮಾಡುತ್ತೇನೆ. ದಯವಿಟ್ಟು ನಿಮ್ಮನ್ನು ಹೇಗೆ ಮತ್ತು ಯಾವಾಗ ಸಂಪರ್ಕಿಸಬೇಕು ಎಂದು ತಿಳಿಸಿ.",
        "injection": "ನಾನು ಈ ಕಾಲೇಜಿನ ಪ್ರವೇಶ ಮತ್ತು ಅಧ್ಯಯನಕ್ಕೆ ಸಂಬಂಧಿಸಿದ ಪ್ರಶ್ನೆಗಳಿಗೆ ಮಾತ್ರ ಸಹಾಯ ಮಾಡಬಲ್ಲೆ.",
        "abuse": "ನಾನು ಪ್ರವೇಶ ಸಂಬಂಧಿತ ಪ್ರಶ್ನೆಗಳಿಗೆ ಸಹಾಯ ಮಾಡಲು ಇದ್ದೇನೆ. ದಯವಿಟ್ಟು ಗೌರವದಿಂದ ಮಾತನಾಡೋಣ.",
        "rate_limited": "ನೀವು ತುಂಬಾ ವೇಗವಾಗಿ ಸಂದೇಶ ಕಳುಹಿಸುತ್ತಿದ್ದೀರಿ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಕಾಯಿರಿ.",
        "unavailable": "ಈಗ ಸ್ವಲ್ಪ ತೊಂದರೆಯಾಗಿದೆ. ನಿಮ್ಮ ಸಂಖ್ಯೆಯನ್ನು ನೀಡಿ, ನಮ್ಮ ತಂಡ ನಿಮಗೆ ಕರೆ ಮಾಡುತ್ತದೆ.",
        "empty": "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ.",
        "too_long": "ಸಂದೇಶ ತುಂಬಾ ಉದ್ದವಾಗಿದೆ. ದಯವಿಟ್ಟು ಒಮ್ಮೆಗೆ ಒಂದು ಪ್ರಶ್ನೆ ಕೇಳಿ.",
        "closed": "ನಮ್ಮ ಪ್ರವೇಶ ತಂಡ {hours} ಲಭ್ಯವಿರುತ್ತದೆ. ಈಗ ಕಚೇರಿ ಸಮಯವಲ್ಲ, ಆದ್ದರಿಂದ ತಂಡ {when} ನಿಮ್ಮನ್ನು "
        "ಸಂಪರ್ಕಿಸುತ್ತದೆ.",
        "not_translated": "ಕ್ಷಮಿಸಿ, ಈ ಉತ್ತರವನ್ನು ಕನ್ನಡದಲ್ಲಿ ನಿಖರವಾಗಿ ನೀಡಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ಪ್ರವೇಶ ತಂಡದವರು ನಿಮ್ಮನ್ನು "
        "ಸಂಪರ್ಕಿಸಲೆ?",
        "language_limited": "ಸದ್ಯಕ್ಕೆ ನಾನು ಕನ್ನಡದಲ್ಲಿ ಶುಲ್ಕ, ಕೋರ್ಸ್ ಅವಧಿ ಮತ್ತು ಪ್ರವೇಶ ದಿನಾಂಕಗಳ ಬಗ್ಗೆ ಉತ್ತರಿಸಬಲ್ಲೆ. ಈ "
        "ಪ್ರಶ್ನೆಗೆ ನಮ್ಮ ಪ್ರವೇಶ ತಂಡ ಕನ್ನಡದಲ್ಲಿ ಸಹಾಯ ಮಾಡುತ್ತದೆ. ಅವರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸಲೆ?",
        "did_you_mean": "ನೀವು {options} ಎಂದು ಕೇಳಿದಿರಾ? ಹೌದು ಎನ್ನಿ, ಅಥವಾ ಪ್ರಶ್ನೆಯನ್ನು ಬೇರೆ ರೀತಿಯಲ್ಲಿ ಕೇಳಿ.",
        "did_you_mean_pick": "ನೀವು ಇವುಗಳಲ್ಲಿ ಒಂದನ್ನು ಕೇಳಿದಿರಾ? {options} ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಿ, ಅಥವಾ ಪ್ರಶ್ನೆಯನ್ನು "
        "ಬೇರೆ ರೀತಿಯಲ್ಲಿ ಕೇಳಿ.",
        "offer_declined": "ಸರಿ. ಇನ್ನೇನು ತಿಳಿಯಲು ಬಯಸುತ್ತೀರಿ?",
        "did_you_mean_no": "ಪರವಾಗಿಲ್ಲ. ದಯವಿಟ್ಟು ಕೋರ್ಸ್ ಹೆಸರನ್ನು ಮತ್ತೆ ಹೇಳಿ, ಅಥವಾ ಪ್ರಶ್ನೆಯನ್ನು ಬೇರೆ ರೀತಿಯಲ್ಲಿ ಕೇಳಿ.",
        "general_label": "{college} ಅಧಿಕೃತ ಮಾಹಿತಿಯಲ್ಲಿ ಇದು ಇಲ್ಲ, ಆದ್ದರಿಂದ ಇದು ಸಾಮಾನ್ಯ ಉತ್ತರ. ದಯವಿಟ್ಟು "
        "ಪ್ರವೇಶ ತಂಡದೊಂದಿಗೆ ಇದನ್ನು ದೃಢಪಡಿಸಿಕೊಳ್ಳಿ.",
        "general_offer": "{college} ಕುರಿತ ನಿಖರ ವಿವರಗಳಿಗಾಗಿ, ಪ್ರವೇಶ ತಂಡದವರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುವಂತೆ ಮಾಡಲೆ?",
        "language_set": "ಸರಿ, ಇನ್ನು ಮುಂದೆ ನಾನು ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸುತ್ತೇನೆ. ನೀವು ಏನು ತಿಳಿಯಲು ಬಯಸುತ್ತೀರಿ?",
        "output_text": "ಸರಿ, ನಾನು ಪಠ್ಯದಲ್ಲಿ ಮಾತ್ರ ಉತ್ತರಿಸುತ್ತೇನೆ.",
        "output_voice": "ಸರಿ, ನಾನು ಧ್ವನಿಯಲ್ಲಿ ಉತ್ತರಿಸುತ್ತೇನೆ.",
        "output_both": "ಸರಿ, ನಾನು ಪಠ್ಯ ಮತ್ತು ಧ್ವನಿ ಎರಡರಲ್ಲೂ ಉತ್ತರಿಸುತ್ತೇನೆ.",
    },
}


def message(key: str, language: str) -> str:
    return MESSAGES.get(language, MESSAGES["en"]).get(key) or MESSAGES["en"][key]


def looks_like_no_answer(text: str) -> bool:
    """The model was told to reply NO_ANSWER; small models also paraphrase a refusal."""
    from .checks import is_refusal

    t = text.strip().strip(".").lower()
    if NO_ANSWER.lower() in t.replace(" ", "_"):
        return True
    if len(t) < 300 and is_refusal(t):
        return True
    return len(t) < 200 and any(
        p in t
        for p in (
            "sources do not",
            "sources don't",
            "not mentioned in the sources",
            "no information",
            "not provided in the sources",
            "i don't have information",
            "does not contain",
            "no information provided",
            "not mentioned",
            "do not mention",
            "does not mention",
            "is not mentioned",
        )
    )


# A labelled general answer when the college's own information has nothing (engine._general_answer;
# only for a college whose pack sets model_policy.general_answers). It is shown under a label that
# says it is not the college's information, and engine.py drops every sentence with a number or a
# promise in it, so a fee, date, code, cut-off or seat count can never come from here.
GENERAL_PROMPT = (
    "You help visitors at the admissions desk of {college}. The college's official information "
    "has nothing on the visitor's question. Give a short, helpful general answer ({length}) from "
    "widely known facts. It may be about admissions and higher education in India (such as what "
    "an entrance exam or a counselling process is and where to check it officially), subjects "
    "and careers, student life and the city, or any general-knowledge question a student or a "
    "parent might ask.\n"
    "Never state anything specific to {college}: no fees, dates, deadlines, codes, cut-offs, "
    "ranks, seat counts, phone numbers, e-mail addresses or names of people. Do not use digits. "
    "Never promise admission, a seat, a scholarship or a job. If the question asks for something "
    "harmful, hateful, sexual, medical or legal advice about the visitor themselves, or you are "
    "not sure of the facts, reply NO_ANSWER only.\n"
    "Write plain sentences, no lists, no Markdown. {language}"
)
# Fixing a question the assistant could not answer before it looks for related questions
# (engine._related; only for a college that uses Gemini). The result is offered to the visitor
# as "Did you mean ...?", never answered unasked, and suggest.corrected rejects a rewrite.
SPELLING_PROMPT = (
    "A visitor typed or spoke this question to a college admissions assistant. Fix spelling "
    "mistakes and misheard words (for example comdk -> COMEDK, hostle -> hostel, nero space -> "
    "aerospace). Keep the meaning and the wording otherwise; do not add or answer anything. If "
    "it is already correct, return it unchanged. Reply with the corrected question only."
)

GENERAL_LANGUAGE = {
    "en": "Reply in English.",
    "hi": "Reply in Hindi (Devanagari script).",
    "kn": "Reply in Kannada (Kannada script).",
}


# Translating an answer that has already passed the sentence checks (engine._translated). The
# engine keeps the translation only if it holds exactly the same numbers and dates.
_TRANSLATE = (
    "Translate the college admissions answer below into simple, natural {language} ({script}), "
    "as a friendly admissions counsellor would say it to a student. Keep every number, amount, "
    "date and e-mail address exactly as written, in the same digits (Western digits 0-9). Keep "
    "programme, course and exam names (B.Tech, MBA, COMEDK, KCET) in English letters. Do not "
    "add, drop or change any information. Reply with the translation only."
)
TRANSLATE_PROMPT = {
    "kn": _TRANSLATE.format(language="Kannada", script="Kannada script"),
    "hi": _TRANSLATE.format(language="Hindi", script="Devanagari script"),
}
# A Hindi or Kannada question, put into English before it is routed, searched and answered
# (engine._english_question): the college's sources are English, and a Kannada query missed the
# page an English one found (16 Nov 2026, RUAS COMEDK code).
QUESTION_PROMPT = (
    "Translate the visitor's message to a college admissions assistant into English. It may be "
    "Hindi or Kannada, in its own script or in English letters, possibly mixed with English. Keep "
    "the names of the college, programmes, courses and exams (B.Tech, MBA, COMEDK, KCET) and every "
    "number exactly as written. Do not answer it and do not add anything. Reply with the English "
    "message only."
)

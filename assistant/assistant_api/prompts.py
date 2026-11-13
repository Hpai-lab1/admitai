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


# Translating an answer that has already passed the sentence checks (engine._translated).
TRANSLATE_PROMPT = {
    "kn": "Translate the college admissions answer below into simple, natural Kannada (Kannada "
    "script). Keep every number, amount, date, programme name and e-mail address exactly as "
    "written, in the same digits. Do not add, drop or change any information. Reply with the "
    "translation only.",
}

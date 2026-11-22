# Widget changes for English / Hindi / Kannada (proposed to Module C)

Module B's side is built and tested (README "Languages: English, Hindi and Kannada"). The widget in
`experience/web` keeps working unchanged: its EN / हिं / ಕ buttons already send `language`, which
is now the reply language for English questions; Hindi and Kannada questions are answered in
their own language; push-to-talk already plays `audio_wav_base64`, which now also comes for Hindi
and Kannada replies when Sarvam is configured. Module B did not edit `experience/`. What follows is
for a Module C pull request.

## 1. The comment at `Widget.tsx` `send()`

"The language the visitor chose decides the reply, whatever the question is typed in" is no longer
true: take the reply language from the answer, not from the button.

```ts
const done = await ask(/* as now */);
const replyLang = (done.language as Lang) ?? lang; // en | hi | kn, decided by Module B
patch((m) => ({ ...m, lang: replyLang }));
// The visitor asked in the conversation ("reply in Kannada"): show it on the buttons.
if (done.language_reason === "requested" && replyLang !== lang) setLang(replyLang);
```

## 2. Output: Text / Voice / Both

A three-way toggle next to the language buttons (`aria-pressed` like them). Default `text` in chat,
`both` for the microphone. Keep it in widget state only (no personal data).

```ts
type Output = "text" | "voice" | "both";
const [output, setOutput] = useState<Output>("text");
// push-to-talk (lib/voice.ts): send it with the recording
form.append("output", output === "text" ? "text" : "both");
form.append("response_language", lang);
```

## 3. Hearing a chat answer

A play button on each finished assistant message, and auto-play when `output` is `voice` or
`both`. The answer's own language picks the voice; a 422 means there is no voice for it here
(no Sarvam key): hide the button, never fall back to the English voice.

```ts
async function speak(text: string, language: Lang, token: string): Promise<Blob | null> {
  const r = await fetch(`${ASSISTANT}/voice/speak`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ text, language }),
  });
  if (r.status === 422) return null; // no voice for this language
  if (!r.ok) throw new Error(`speak ${r.status}`);
  return r.blob(); // audio/wav
}
```

## 4. Strings (STRINGS in Widget.tsx)

| key | en | hi | kn (needs a native speaker's review) |
|---|---|---|---|
| `outputText` | Text | लिखित | ಪಠ್ಯ |
| `outputVoice` | Voice | आवाज़ | ಧ್ವನಿ |
| `outputBoth` | Both | दोनों | ಎರಡೂ |
| `play` | Listen | सुनें | ಕೇಳಿ |

## Tests (vitest)

- `done.language` sets the message's `lang` attribute even when another button is pressed.
- `language_reason: "requested"` moves the pressed button.
- `output: "text"` sends `output=text` and plays nothing; a 422 from `/voice/speak` hides the
  play button.

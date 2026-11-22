# Accessibility test script (keyboard and screen reader)

Roadmap item 2. An automated WCAG 2.2 A/AA check (axe-core) of 7 pages, light and dark, finds no
violations, but automated checks catch only about a third of problems. This script is for the
person who checks the rest by hand. It takes about half a day.

## Setup

- **Screen readers:** VoiceOver on macOS (Cmd+F5) with Safari; NVDA on Windows with Chrome or
  Firefox; VoiceOver on an iPhone with Safari, if available.
- **Pages:** the demo college site (`/demo-site`) with its widget, and the staff console
  (`/console`), from a freshly prepared demo.
- **Rules for the tester:** keyboard only, no mouse or trackpad. Do each part once with the
  screen reader on, and once with it off and the browser zoomed to 200%.
- **Record** each problem in the table at the end, with the WCAG criterion it breaks.

## A. The visitor (widget)

| # | Do this | It passes if | WCAG |
|---|---|---|---|
| A1 | From the top of the college page, press Tab until you reach **Ask Admissions**, then press Enter | Focus is visible at every step; the chat opens and focus moves into it | 2.4.7, 2.4.3 |
| A2 | Listen to what is announced when the chat opens | The greeting is read; the question box is announced with its label | 4.1.2, 1.3.1 |
| A3 | Type "What is the hostel fee?" and press Enter | "Thinking" is announced once; the answer is read when it arrives, not word by word, and not twice | 4.1.3 |
| A4 | Tab to the citation chip **1** after the answer, and press Enter | It is announced as "Source 1: …" with the document's title | 1.1.1, 4.1.2 |
| A5 | Tab to 👍 and 👎, and choose 👎, then a reason | Each button says what it does; the thanks is announced | 4.1.2, 4.1.3 |
| A6 | Ask something unknown ("Is there a swimming pool?"), then choose **Talk to a person** | The offer is announced; the contact form opens with focus on its first field | 3.3.2, 2.4.3 |
| A7 | In the form, submit without ticking the required consent | The button stays disabled, or the error is announced and linked to the field | 3.3.1, 3.3.3 |
| A8 | Choose **under 18** and go through the guardian steps | Every step is announced; no field asks for the student's own phone or e-mail | 3.3.2 |
| A9 | After a callback request, wait for the banner | "Connecting you to Admissions…" is announced, then the staff member's name once claimed | 4.1.3 |
| A10 | Press the microphone button (with a microphone) | Its state is announced (listening, thinking, speaking); captions appear as text | 1.2.4, 4.1.2 |
| A11 | Open **Rate this chat** and choose 4 stars | Each star says its number; the thanks is announced | 4.1.2 |
| A12 | Press Escape | The chat closes and focus returns to **Ask Admissions** | 2.1.2, 2.4.3 |
| A13 | Zoom to 200% and repeat A3 and A6 | Nothing is cut off or overlaps; no sideways scrolling inside the chat | 1.4.4, 1.4.10 |
| A14 | Switch the computer to dark mode and repeat A3 | All text is readable | 1.4.3 |

## B. Staff (console)

| # | Do this | It passes if | WCAG |
|---|---|---|---|
| B1 | Sign in as `officer@example.com` with the two-factor code | Field labels and errors are announced; focus moves to the code field | 3.3.1, 3.3.2 |
| B2 | Use **Skip to content** from the top of the page | It is the first Tab stop and moves focus to the main content | 2.4.1 |
| B3 | On the home page, read **Needs you today** | It is announced as a section with its heading; each item is a link that says what it opens | 2.4.6, 2.4.4 |
| B4 | In the queue, use the Open and Resolved tabs with the arrow keys and Tab | Tabs are announced as tabs, with the selected one; "Only mine" is a separate checkbox | 4.1.2 |
| B5 | Use the shortcuts j, k, Enter and c on the queue | They work, and do nothing while typing in a field | 2.1.1, 2.1.4 |
| B6 | Open a task, write a reply, tick "also send by e-mail", save | The result is announced; nothing depends on colour alone | 4.1.3, 1.4.1 |
| B7 | In **Approvals**, press **Draft from documents** | "Drafting…" and the result (draft or reason) are announced | 4.1.3 |
| B8 | Approve a change with a written answer | The confirmation and the "goes live after testing" note are announced | 4.1.3 |
| B9 | On **Analytics**, move through the tables | Column headers are announced with each cell | 1.3.1 |
| B10 | On **Your account**, add a photo, then turn notifications on | The file picker is reachable and labelled; the result is announced | 2.1.1, 4.1.3 |
| B11 | Turn on dark mode and visit each page | All text, buttons and badges are readable | 1.4.3, 1.4.11 |

## Results

| # | Browser and screen reader | Problem | WCAG | Severity (blocker / serious / minor) |
|---|---|---|---|---|
| | | | | |

A **blocker** stops someone finishing a task (asking, requesting a callback, the guardian flow,
signing in, replying). Fix blockers before the first real college goes live; serious problems
during the pilot.

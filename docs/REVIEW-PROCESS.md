# AdmitAI — GitHub and review process

**For:** the three student developers. **From:** your project guide.
**Repository:** https://github.com/Hpai-lab1/admitai (public)

How we use GitHub: one-time setup, the daily flow, how to review, and when the guide gets involved.

---

## 1. Your team

| GitHub | Module | Folder | Branch prefix | Your first peer reviewer |
|---|---|---|---|---|
| `adityadk03` | A — Knowledge & Learning | `knowledge/` | `a/` | `ramitharavichandra` |
| `koushikkumar2004` | B — Assistant Engine & Channels | `assistant/` | `b/` | `adityadk03` |
| `ramitharavichandra` | C — Experience & Operations | `experience/` | `c/` | `koushikkumar2004` |

**Any one teammate's approval is enough to merge.** The "first peer reviewer" is simply who looks first.

---

## 2. One-time setup

### Step 1. Accept the invitation
Accept the `Hpai-lab1` organisation invitation from the e-mail, or open https://github.com/Hpai-lab1 while signed in.

### Step 2. Keep your e-mail private
The repository is public, so every commit shows its author e-mail.
1. GitHub → **Settings → Emails** → turn on **Keep my email addresses private** and **Block command line pushes that expose my email**.
2. Copy your private address from that page (it ends with `@users.noreply.github.com`).

**ON YOUR COMPUTER (Git Bash on Windows):**

```bash
git config --global user.name "Your Name"
git config --global user.email "YOUR-ID+YOUR-GITHUB-USERNAME@users.noreply.github.com"
```

### Step 3. Clone and turn on the safety checks

```bash
git clone https://github.com/Hpai-lab1/admitai.git
cd admitai
python -m pip install pre-commit
pre-commit install
```

Every commit now runs formatting, lint, a **secret scan**, and a check that stops commits to `main`. Never skip it with `--no-verify`.

### Step 4. Turn on notifications
On the repository page click **Watch → Participating and @mentions** (or **All activity**), and keep GitHub e-mail notifications on, so you see review requests.

---

## 3. Daily flow

1. **Update and branch** — one branch per task, never work on `main`:
   ```bash
   git switch main
   git pull
   git switch -c a/short-topic      # b/ or c/ for your module
   ```
2. **Commit** small, focused changes. Start the message with the requirement ID when there is one, e.g. `F4.3: import program catalogue CSV`.
3. **Push** your branch:
   ```bash
   git push -u origin a/short-topic
   ```
4. **Open a pull request** from the link Git prints. Fill in the template: what it changes, requirement IDs, how you tested it.
5. **Wait for the checks** to turn green. `guide-gate` also asks your teammates to review automatically.
6. **Get one teammate's approval**, then click **Squash and merge** and delete the branch.

`main` is protected: direct pushes, force pushes and merges without approval are blocked for everyone.

---

## 4. Reviewing a teammate's pull request

When you're asked to review:

1. Open the pull request → **Files changed**.
2. Check:
   - it does what the description says, and the requirement IDs are right;
   - the tests exist — **check out the branch and run them**, don't just read the code:
     ```bash
     git fetch
     git switch their-branch-name
     ```
   - it stays inside the author's module folder;
   - nothing private: no keys, passwords, `.env` files, real names, phone numbers or e-mails, server addresses;
   - the API still matches its file in `contracts/`.
3. Click **Review changes** and choose:
   - **Approve** — when it's ready. (A *Comment* does **not** count as an approval.)
   - **Request changes** — when something must be fixed; say exactly what.
4. Reply within **one working day**, so nobody is blocked.

Your approval is the project's quality check. If you approve something, you share responsibility for it.

---

## 5. What merging needs

| Pull request | Needs |
|---|---|
| Changes only inside module folders, docs, tests | **1 teammate's approval** + checks green |
| Touches a **shared path**: `contracts/`, `deploy/`, `eval-data/`, `.github/`, `.claude/`, `CLAUDE.md`, `SECURITY.md`, `.gitignore`, `.pre-commit-config.yaml` | Teammate approval **+ the guide's approval** (requested automatically) |
| Flagged by **`guide-gate`** | Blocked until the flagged item is removed, or the guide clears it |

## 6. The checks on every pull request

| Check | What it does | If it's red |
|---|---|---|
| **`ci`** | Formatting, lint, secret scan, contract files, unit tests | Open the failed run, read the first error, fix, push again |
| **`guide-gate`** | Looks for a key or token, phone number, real e-mail, internal IP address, `.env` file, or Word/PDF/Excel/database file. Also requests reviewers | The PR is labelled **`needs-guide`** and the guide is notified. Remove the item and push again; for a false positive, ask the guide — only the guide's **`guide-cleared`** label unblocks it |
| **CodeQL** | Looks for security problems in code | Read the finding; fix it or ask the guide |

`guide-gate` is a plain script (no AI). Its comment hides most of any matched value, so it doesn't republish it.

---

## 7. The public-repository rules

- **No secrets:** no passwords, API keys, tokens or `.env` files — in code, tests, docs, issues or screenshots.
- **No real personal data:** test data only. Real questions are anonymised before they reach `eval-data/`.
- **No lab details:** no server addresses, account names or anything from the private repository.
- **Pushed a secret by mistake?** Tell the guide **immediately**. The secret must be revoked — deleting the commit is not enough, because public commits are copied within minutes.
- **Found a security problem?** Don't open a public issue; use **Security → Report a vulnerability**.

---

## 8. If something goes wrong

| Problem | What to do |
|---|---|
| The invitation isn't in your e-mail | Open https://github.com/Hpai-lab1 while signed in, or ask the guide to resend it |
| `git push` is rejected for `main` | Expected. Create a branch and open a pull request |
| Push rejected: "email privacy restrictions" | Set your Git e-mail to the `@users.noreply.github.com` address (Step 2), amend the commit (`git commit --amend --reset-author --no-edit`), push again |
| Push blocked: "secret detected" | GitHub's push protection found a secret. Remove it from the commit and tell the guide |
| `pre-commit` fails on commit | Read the message; it often fixes formatting itself. `git add -A` and commit again |
| "Review required" even after an approval | The approval was a *Comment*, or the PR touches a shared path and needs the guide too |
| `guide-gate` is red | See §6 |
| Your branch is behind `main` | `git switch main && git pull && git switch your-branch && git merge main`, fix any conflicts, push |
| Not sure a change is allowed | Stop and ask the guide first |

# Decision record: secrets at rest — where `token.json` and friends live in production

**Status:** RECOMMENDATION — not yet ratified. This document proposes an approach for the team to review and decide on; nothing here has been agreed or implemented. It covers checklist item 5.6.

## Context (verified in this session)

`backend/oauth_setup.py` writes a Google OAuth refresh token to disk and says so explicitly, at the end of `main()`:

```python
print(f"Success. Refresh token saved to {TOKEN_FILE}.")
print("Keep this file out of version control — it's a live credential.")
```

That is the exact text as it appears in the file today (`backend/oauth_setup.py:53-54`). The scope requested for that token is declared a few lines earlier, `backend/oauth_setup.py:35`:

```python
SCOPES = ["https://www.googleapis.com/auth/business.manage"]
```

`business.manage` is not a read-only scope — it is the scope Business Profile management calls need, per the comment directly above it. A `token.json` on disk is therefore a live, refreshable credential capable of acting on Remedy's Business Profile listings, not an inert config value.

**`.gitignore` coverage was verified, not assumed.** There is exactly one `.gitignore` in this repo — at the repo root (`.gitignore`, checked via `ls backend/.gitignore`, which does not exist). It reads, in full:

```
__pycache__/
*.pyc
.env
client_secret.json
token.json
venv/
.venv/
```

`git check-ignore -v` was run against all three files under `backend/` to confirm the root pattern actually applies to that subdirectory (a bare filename pattern in a root `.gitignore` matches at any depth) rather than assuming it from the text alone:

```
.gitignore:5:token.json         backend/token.json
.gitignore:4:client_secret.json backend/client_secret.json
.gitignore:3:.env                backend/.env
```

So the oauth_setup.py comment's claim is correct as far as it goes: `.gitignore` does keep these three files out of version control, today, for anyone cloning this repo. That is a genuinely different question from where the file lives once something other than a developer's laptop is running these scripts, which is the gap this item is about.

## The gap

Being gitignored means the credential never enters source control. It says nothing about where the file lives on the machine that runs `oauth_setup.py` and `fetch_owned_reviews.py`, and nothing changes about that as this code moves from "a script one engineer runs locally" to "a job running somewhere in production." Today, `token.json` and `client_secret.json` sit as plain files next to the code, readable by anything with filesystem access to that machine — fine for a single developer's laptop during local development, not fine for a shared server, a container image, or any environment where more than one process or one person has access to the filesystem.

No hosting platform has been chosen yet — the roadmap's vendor/build decision is still listed `Blocked` with no owner-assigned deadline (checklist item 1.4). That is exactly why this document frames the recommendation generically rather than naming a specific secrets manager: naming one now would be guessing at an infrastructure decision that has not been made.

## Options considered

- **Status quo: file on disk, next to the code.** Zero setup cost, and it is genuinely fine for local development — that's what it's being used for today, and nothing here suggests changing local dev workflow. Wrong for production: no access control beyond filesystem permissions, no audit trail of who read it or when, no rotation mechanism, and it travels with the machine (or the container image, if someone naively bakes it in) rather than staying tied to the deploy.
- **Environment variables injected at deploy time.** Better than a bare file — the secret no longer sits in a file an attacker can just `cat`, and most deploy platforms inject env vars per-environment without a file ever touching disk. Still visible to anything that can read the process's environment (a debugging endpoint that dumps env, a crash reporter that logs it, a child process that inherits it, `/proc/<pid>/environ` for anyone with sufficient access on the host) — an improvement in *where* the secret sits, not a fix for *who else can see it*.
- **A real secrets manager appropriate to the hosting platform ultimately chosen** (examples only, not a commitment to any one of them: AWS Secrets Manager / Parameter Store, GCP Secret Manager, Azure Key Vault, HashiCorp Vault, or a PaaS's built-in secrets store). Best option in principle — access-controlled, auditable, rotatable, and the credential is fetched at runtime rather than living on disk or in the process environment at rest. Underspecified today because it depends on a hosting decision (checklist 1.4) that has not landed; naming a specific product now would be a guess dressed up as a recommendation.

## Reasoning

The risk here is not hypothetical or generic — it is the literal one the script's own comment already names: a live, `business.manage`-scoped credential capable of acting on Remedy's Business Profile listings, sitting as a bare file on whatever machine runs the OAuth flow. That risk is acceptable during local development (one developer, one laptop, `.gitignore` doing its job) and stops being acceptable the moment more than one person or process shares that machine, which is what "production" means here regardless of which platform gets chosen.

Because the hosting/vendor decision (checklist 1.4) is still open, this document cannot responsibly recommend a specific product without guessing at infrastructure that has not been decided. What it can say with confidence: whatever platform is chosen, prefer its native secrets manager over environment variables, and prefer environment variables over a bare file — that ordering holds regardless of which specific vendor or platform 1.4 lands on.

## Recommendation

Adopt, in order of preference once a hosting platform exists:

1. **The chosen platform's native secrets manager** for `token.json`'s contents, `client_secret.json`'s contents, and any `.env` values with the same sensitivity — fetched at runtime, not written to disk on the production host at all if the platform supports that.
2. **Environment variables injected at deploy time**, only if (1) is unavailable on the chosen platform or not yet wired up — an acceptable interim step, not an end state.
3. **Never** a plain file checked into an image or left on a shared production host, even though `.gitignore` already prevents it from reaching source control — those are two different protections and this decision is about the second one.

Local development is explicitly out of scope for a change here — the current file-on-disk-plus-`.gitignore` setup is the right amount of ceremony for one developer's laptop, and nothing above should be read as asking to add secrets-manager overhead to local dev.

## What would change this

The hosting/vendor decision (checklist item 1.4) landing on a specific platform with its own specific secrets convention. Once that happens, this document should be updated to name that platform's mechanism specifically (e.g., "AWS Secrets Manager, fetched via `boto3` at process start" rather than "a real secrets manager") — the generic framing here is a placeholder for that update, not a permanent state.

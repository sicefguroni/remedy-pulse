# Credentials distributed inside a review archive

**Date raised:** 2026-09-14
**Status:** contained — keys rotated by the reviewer; prevention landed in this change
**Severity:** high (live third-party credentials left the team)

## What happened

A zip of this repository was sent out for review. It contained
`backend/.env`, holding live values for:

| Credential | Scope of exposure |
| --- | --- |
| `GOOGLE_PLACES_API_KEY` | Google Places API, billing-enabled project |
| `GNEWS_API_KEY` | GNews free tier, 100 req/day |
| `GROQ_API_KEY` | Groq API, classification and topic tagging |

The reviewer identified the exposure and rotated the keys.

## Why it happened

Not a misconfiguration. `.gitignore` correctly listed `.env`, `token.json`
and `client_secret.json`, and **none of them were ever committed** —
verified against the full history:

```
git log --all --diff-filter=A --name-only -- '*.env' 'token.json' 'client_secret.json'
(no results)
```

The archive was built by compressing the project **folder**. That
operation copies the working directory and does not consult `.gitignore`,
so it picked up exactly the files that are supposed to exist locally and
never leave the machine.

The root cause is therefore the packaging step, not the ignore rules. Any
control that depends on someone remembering which files to exclude while
right-clicking a folder will fail again the same way.

## What changed

**1. Archives are built from git, not from the folder.**
`scripts/package_release.py` produces the archive with `git archive`,
which emits only tracked files at a given ref. An untracked file is not
filtered out — it is never a candidate. This is the actual fix.

```
python scripts/package_release.py            # -> dist/remedy-pulse-<sha>.zip
```

**2. The finished archive is scanned independently.**
The same script re-opens the archive it just wrote and checks for
forbidden filenames (`.env`, `token.json`, `client_secret.json`, private
keys) and for credential-shaped content (Google, Groq, GitHub, AWS,
Slack, OpenAI-style keys, PEM blocks). Any finding **deletes the archive**
and exits non-zero, so a failed build cannot be sent by someone reaching
for the file later.

Verified by planting a fake `.env` and a key pasted into a `notes.md`
into a copy of a real archive; both were caught. The placeholder values in
`.env.example` do not trip it — patterns match on vendor prefix and
length, so `your_groq_api_key_here` is not a finding, while a real key
pasted into that same file would be.

**3. CI fails on a committed secret.**
`.github/workflows/ci.yml` runs the same scanner on every push and pull
request, before the dependency install so it fails in seconds.

**4. An archive made by hand can still be checked.**

```
python scripts/package_release.py --scan-only some-archive.zip
```

## What was deliberately not done

**History was not rewritten.** Nothing sensitive was ever committed, so
there is nothing in history to remove. A force-push across a shared
repository has real costs and would buy nothing here.

**No secrets manager was introduced.** The exposure route was a local
file leaving the machine, not the way the file is read. `.env` locally and
platform environment variables in deployment (already supported —
`GOOGLE_TOKEN_JSON` exists precisely for this, see `.env.example`) remain
the right shape at this size. Adding a vault would not have prevented
this incident.

## If it happens again

1. **Rotate first, everything in the file, before any cleanup.** Assume
   the keys are compromised from the moment the archive left.
   - Google: Cloud Console → APIs & Services → Credentials → delete and
     recreate the key; re-apply the HTTP-referrer/API restrictions.
   - GNews: gnews.io dashboard → regenerate.
   - Groq: console.groq.com → API Keys → revoke and create.
2. Update `backend/.env` locally and the deployment's environment
   variables.
3. Check third-party usage dashboards for calls you cannot account for.
4. Only then worry about the archive itself.

## Standing rules

- Never build a review archive by compressing the folder. Use
  `scripts/package_release.py`.
- A real credential never goes in any file that is tracked, including
  `.env.example` "just to test".
- `SESSION_SECRET_KEY` is generated, never invented:
  `python -m app.admin generate-secret`.

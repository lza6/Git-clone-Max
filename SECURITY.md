# Security Policy

## Supported versions

This project ships a single self-contained executable built from the latest
release. Only the **latest released version** receives security fixes.

| Version | Supported |
|---------|-----------|
| latest release | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately via GitHub's
[Security Advisories](https://github.com/lza6/Git-clone-Max/security/advisories/new)
for this repository.

Useful details to include:

- Affected version (`Git-clone-Max.exe` → Help → About, or `python -m gcm --version`)
- Steps to reproduce, and whether the issue requires local access or a
  malicious remote repository
- Any relevant entry from the diagnostic bundle (Settings → 导出诊断包),
  which is **already redacted** at the formatting layer

Expect an initial response within 7 days.

## Scope

This is a local desktop tool. The security boundary that matters most:

- **Credentials** — tokens, `host_tokens`, SSH key paths and proxy URLs are
  encrypted at rest where a DPAPI/keyring backend exists, and are redacted at
  every outbound boundary (`gcm/util/redact.py`, `applog.RedactingFormatter`).
  A leak of these into UI, logs, CSV/XLSX, MCP or CLI output is a valid report.
- **Mirror routing** — third-party mirrors are never used when a token or host
  credential is present, because a mirror would see the `Authorization` header.
  A path that sends credentials to a mirror is a valid report.
- **Local HTTP API / SSE** — binds to loopback only and has **no
  authentication** by design. Exposure via a reverse proxy is a deployment
  misconfiguration, not a vulnerability in this project, but a way to bypass
  the loopback check *is* a valid report.
- **Cloned repository content is untrusted.** This tool runs system `git`
  against remotes you point it at. It does not execute hooks from cloned
  repositories; a path that does so is a valid report.

## Out of scope

- Issues requiring an attacker who already controls the local machine or the
  user's `settings.json`
- Missing hardening on endpoints that are loopback-only and unauthenticated by
  documented design (see `docs/agent-contract.md` §5)
- Vulnerabilities in `git` itself — report those upstream

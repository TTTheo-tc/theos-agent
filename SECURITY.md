# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public issue**
2. Email the maintainer directly via [GitHub profile](https://github.com/TTTheo-tc)
3. Include: description, reproduction steps, impact assessment

We will acknowledge receipt within 48 hours and provide a timeline for the fix.

## Security Features

- API keys and OAuth tokens encrypted at rest (AES-256-GCM)
- Config secrets encrypted via `ConfigSecretsManager`
- SSRF protection on web_fetch and http_request tools
- Credential leak detection (regex + Shannon entropy)
- Session history scrubbing for sensitive data
- Autonomy policy levels (READONLY / SUPERVISED / AUTONOMOUS)

---
name: Bug report
about: Report something that's broken
title: "[Bug] "
labels: bug
assignees: ''
---

## What happened

<!-- A clear, one-paragraph description of the bug. -->

## Reproduction steps

1. `git clone https://github.com/Akichoooo/subclaw`
2. `cd subclaw/proxy`
3. `cp keys.example.json keys.json`  *(and put a real key in)*
4. `python app.py`
5. `curl -X POST http://localhost:4748/v1/messages -d '...'`
6. → 5xx / wrong response / crash

## Expected behavior

<!-- What you thought would happen. -->

## Actual behavior

<!-- What actually happened. Include any error output, log lines, and the full response body. -->

## Environment

- subclaw version: `git rev-parse HEAD` in the repo
- Python version: `python --version`
- OS: (Windows / macOS / Linux — and which distro if Linux)
- Install method: (native / Docker / WSL)
- Claude Code version (if relevant): `<version>`

## Logs

```text
<paste relevant log lines — set LOG_LEVEL=DEBUG for verbose output>
```

## Anything else

<!-- Screenshots, related issues, workarounds you've tried. -->

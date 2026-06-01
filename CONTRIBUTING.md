# Contributing to subclaw

Thanks for your interest in subclaw. We welcome bug reports, feature requests, documentation improvements, and code contributions.

## Code of Conduct

This project follows a [Code of Conduct](.github/CODE_OF_CONDUCT.md). By participating, you agree to its terms.

## How to contribute

### 🐛 Report a bug
Use the [bug report template](https://github.com/Akichoooo/subclaw/issues/new?template=bug_report.md). Include:
- subclaw version (`git rev-parse HEAD`)
- Python version (`python --version`)
- Operating system
- Steps to reproduce
- Expected vs actual behavior
- Relevant log lines (set `LOG_LEVEL=DEBUG` to get verbose output)

### 💡 Request a feature
Use the [feature request template](https://github.com/Akichoooo/subclaw/issues/new?template=feature_request.md). Explain the problem first, then the proposed solution.

### 🔒 Report a security issue
**Do not file a public issue.** Follow the [security policy](.github/SECURITY.md) to report privately.

### 📝 Improve documentation
Documentation PRs are the easiest and most appreciated contributions. Typos, clarifications, new examples — all welcome.

### 🔧 Submit code
1. Fork the repo.
2. Create a feature branch: `git checkout -b fix/short-description` or `feat/short-description`.
3. Make your changes.
4. **Add or update tests** if your change touches proxy logic, session routing, or protocol translation.
5. Run the existing test suite (if present) to confirm no regressions.
6. Commit with a clear message: `git commit -m "fix(proxy): correct session rebind on 429"`.
7. Push and open a PR using the [PR template](.github/PULL_REQUEST_TEMPLATE.md).

## Development setup

```bash
git clone https://github.com/Akichoooo/subclaw.git
cd subclaw/proxy
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Drop a real or test key into keys.json
python app.py
```

The proxy runs on `http://localhost:4748` by default. Endpoints worth knowing:
- `GET /health` — liveness
- `GET /stats` — cache hit rate, cost, key pool state
- `GET /dashboard` — human-readable HTML dashboard
- `GET /models` — discovered models and their capabilities
- `POST /v1/messages` — Anthropic Messages API passthrough
- `POST /v1/chat/completions` — OpenAI Chat Completions passthrough

## Coding conventions

- Python 3.9+ (no walrus abuse; type hints preferred)
- Async I/O for any network call
- Structured logging via the existing `logger` (do not `print` in production paths)
- All new public functions should have a one-line docstring
- Comments in English. Chinese is fine in user-facing README/docs; code stays English.

## Project structure

```
subclaw/
├── proxy/                 FastAPI gateway (the backend)
│   ├── app.py             All proxy logic in one file for now
│   ├── requirements.txt
│   └── keys.example.json
├── cli-skills/            Frontend slash commands
│   ├── claude/subclaw.md  /subclaw command definition
│   └── run-claw-pool.sh   Worker pool driver
├── docs/                  Marketing + technical documentation
├── .github/               Community templates and policies
├── README.md              English
├── README_zh.md           中文
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE
```

## Pull request checklist

- [ ] Branch is up to date with `main`
- [ ] Code follows existing style (no `print`, no commented-out blocks, no dead code)
- [ ] New behavior is covered by a test (where the test infrastructure exists)
- [ ] Docs updated if you changed user-facing behavior
- [ ] Commit messages are clear and prefixed (`feat:`, `fix:`, `docs:`, `chore:`)
- [ ] No secrets, real API keys, or `.env` files in the diff

## Release process

1. Bump version in `CHANGELOG.md` under `[Unreleased] → [X.Y.Z]`
2. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z: short summary"`
3. Push tag: `git push origin vX.Y.Z`
4. GitHub Actions (when configured) builds and publishes the release

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

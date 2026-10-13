### Fixed

- **`switchboard update` passes `--refresh-package switchboard-chat` to uv.** Previously, uv could use its cached PyPI index for up to 10 minutes and report that switchboard was already the newest version right after a release was published.

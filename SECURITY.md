# Security policy

## Supported versions

Only the [latest release](https://github.com/eirikosten75/cockpitlink/releases/latest) receives fixes.

## How the bridge is exposed

- By default the control panel and the HTTP API listen on `127.0.0.1:8765` only (UDP on port 8766), so they cannot
  be reached from other computers on the network. If you change `api.host` in `config.json` to open it up, anyone who
  can reach that address can control your LEDs and change your config – only do that on a network you trust.
- Control-panel endpoints that change things require the header `X-Bridge-UI: 1`, so that other websites open in your
  browser cannot change your config.
- The bridge runs as your own Windows user and does not need admin rights. *Start with Windows* writes only to your
  user's `Run` key in the registry.

## Reporting a vulnerability

Please **do not** report security problems in a public issue.

Use GitHub's private reporting instead: go to the
[Security tab](https://github.com/eirikosten75/cockpitlink/security) and choose **Report a vulnerability**.
Describe the problem, how to reproduce it and which version you used.

This is a hobby project, so there is no guaranteed response time, but reports are taken seriously and you will get an
answer as soon as possible.

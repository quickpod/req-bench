# ReqBench

A fast, **offline**, **100% open-source** REST & GraphQL API client for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/req-bench).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Build and send HTTP/REST and GraphQL requests with headers, params, auth and bodies; organize them into collections with environment variables; inspect responses (pretty JSON, headers, timing); replay from history; and generate client code snippets. A local Postman alternative — nothing is synced to any cloud.

## Install

Download **`ReqBench-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/req-bench) or the [GitHub release](https://github.com/quickpod/req-bench/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python req_bench_app.py          # GUI
python -m reqbench --help    # CLI
```


## Features

- **REST requests** — any method (GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS) with query params, custom headers, and a JSON, form, or raw body. A normal HTTP status (404, 500, …) is shown as a response, never an error; only real transport failures (no connection, timeout, bad URL) surface as a clean message.
- **GraphQL** — send a query/mutation with variables as a proper JSON POST.
- **Auth** — HTTP Basic and Bearer-token, applied automatically.
- **Collections & environments** — save named requests into collections and switch the active environment; `{{variable}}` placeholders in the URL, headers, params, and body are filled from the active environment on send.
- **History** — every send is logged; review, load back into the builder, or replay byte-for-byte.
- **Response viewer** — status, timing and size, with pretty-printed JSON and the full response headers.
- **Code generation** — turn any request into a `curl`, Python `requests`, or JavaScript `fetch` snippet.
- **Desktop GUI** — a pure standard-library tkinter app with light/dark QuickOpen theming, a sidebar (Request, Collections, History, Code-gen), and threaded sending with a spinner and Cancel so the window never freezes.
- **Fully offline** — nothing is ever synced to any cloud. Collections, environments and history live under `%LOCALAPPDATA%\ReqBench` (or `~/.reqbench`).

## CLI examples

```sh
# Send a GET with a header and a query param
python -m reqbench send get https://api.example.com/users -H "Accept: application/json" -q "page=2"

# POST a JSON body with a bearer token
python -m reqbench send post https://api.example.com/items --json '{"name":"widget"}' --auth bearer:TOKEN

# GraphQL query with variables
python -m reqbench graphql https://api.example.com/graphql --query '{ me { name } }' --variables '{"id":"7"}'

# Environments: define a variable, activate it, then use {{host}} in a request
python -m reqbench env setvar dev host https://staging.example.com
python -m reqbench env set dev
python -m reqbench send get "{{host}}/health"

# Collections: save a request, list, and run it
python -m reqbench collection save MyAPI health get "{{host}}/health"
python -m reqbench collection list
python -m reqbench collection run MyAPI health

# Replay from history
python -m reqbench history list
python -m reqbench history replay 0

# Generate a code snippet (curl | python | javascript)
python -m reqbench codegen python post https://api.example.com/items --json '{"a":1}'

# Save a response body to a file instead of printing it
python -m reqbench send get https://api.example.com/report -o report.json
```

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.

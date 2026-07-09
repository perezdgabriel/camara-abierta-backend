# LLM Lambda hang investigation — camara-abierta-backend

**Status:** Partially resolved. Two real bugs found and fixed. One (bill 378) and one (bill 214)
hang remain **unresolved and unreproduced outside AWS**, despite extensive isolation testing.
Written for handoff to a second engineer.

**Date range:** 2026-07-08 → 2026-07-09
**Account:** AWS 697359332290, us-east-1
**Repo:** camara-abierta-backend (FastAPI + Celery, deployed as AWS Lambda via CDK — see ADR-0022)

---

## 1. How this started

Two CloudWatch alarms fired in production:

```
"CamaraCompute-LlmDlqDepth3AB3DEE1-8nnhSm0TcEez" — ALARM 2026-07-08 17:28:16 UTC
  Threshold Crossed: 1 datapoint [1.0] was greater than the threshold (0.0)

"CamaraCompute-LlmErrorsC28A0D15-qPq5sNLqJkzN" — ALARM 2026-07-09 13:07:19 UTC
  Threshold Crossed: 1 datapoint [2.0] was greater than or equal to the threshold (1.0)
```

These watch the AI bill-summary pipeline, enabled in production the day before (commit
`d55efc1`, "enable ai summaries on production").

## 2. Architecture (relevant slice)

```
SQS LlmQueue (visibility_timeout=360s)
  → LlmFn (Lambda, DockerImageFunction, memory_size=1024MB, timeout=300s, batch_size=1)
      → app/lambdas/llm.py: handler() looks up a Celery task by name from the SQS
        message body {"task": ..., "args": [...], "kwargs": {...}} and runs it
        synchronously via task.apply(...).get()
      → app/tasks/bills.py: generate_bill_summary_layer(bill_id, kind)
          - kind="proposal"   → _generate_proposal_layer()
          - kind="amendments" → _generate_amendments_layer()
  → after 3 failed deliveries (max_receive_count=3) → LlmDlq (retention 14 days)
```

`_generate_proposal_layer`: fetch bill's `full_text_url` → download PDF → extract plain text
(`app/services/pdf.py::extract_text_from_bytes`, per-page `page.extract_text()`) → truncate to
`settings.ai_summary_max_input_chars` (150,000) → query existing topics from DB → call Claude
(`app/services/llm.py::generate_proposal_summary` → `_claude_tool_call` → forced tool-use via
`tools=[PROPOSAL_TOOL]`, `tool_choice={"type":"tool","name":"record_proposal_summary"}`,
`max_tokens=2048`).

`_generate_amendments_layer`: fetch each `comparison`-type document URL for the bill → extract
table-based text (`extract_comparado_text_from_bytes`, uses pdfplumber's `page.find_tables()` +
`table.extract()` per page to isolate the right-hand "amendment" column) → truncate → call Claude
similarly.

Both Lambda functions egress through a **single NAT instance** (`fck-nat`, a community
open-source NAT AMI, `t3.micro`, ASG min=max=1 self-healing) — the sole path to the internet
(Anthropic's API, the Senate's document microservice, Secrets Manager/SSM) for every Lambda in
the private subnets, and also doubles as the SSM bastion for the RDS tunnel.

Anthropic client construction (`app/services/llm.py::_claude_client`):
```python
return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=120.0)
```

## 3. Initial triage

The DLQ (redriven via console mid-investigation) originally held two messages:
- `generate_bill_summary_layer(214, "proposal")` — bill 214, bulletin 18216-05, "Para la
  reconstrucción nacional y el desarrollo económico y social" (an economic-reform bill)
- `generate_bill_summary_layer(378, "amendments")` — bill 378, bulletin 18036-05

## 4. Confirmed bug #1 (fixed): proposal layer never truncated its prompt

`settings.ai_summary_max_input_chars = 150_000` (comment: "Cap on the joined comparado text
sent to Claude for the amendments layer") was **only ever applied in `_generate_amendments_layer`**
via `_truncate_to_budget`. The proposal layer sent `full_text` to Claude completely uncapped.

Bill 214's full extracted PDF text: **425,443 characters (~121K tokens)** — sent in one shot,
with the default Anthropic SDK `max_retries=2` (3 total attempts) each potentially eating the
full client `timeout=120.0`, comfortably exceeding the Lambda's 300s budget on its own.

**Fix** (`e823b9e`): apply `_truncate_to_budget` to the proposal layer too, threading a
`truncated` flag through to `generate_proposal_summary()` (which now prefixes the prompt with a
"this is a partial excerpt" note, mirroring what the amendments layer already did) and into
`_persist_layer`. Also added `timeout=120.0` explicitly to the Anthropic client in the same
commit (previously unset, relying on SDK defaults).

This is a real, valid fix, independently useful regardless of everything below. **It did not
fully resolve the incident** — see §6.

## 5. Confirmed bug #2 (unresolved): bill 378's comparado extraction hangs on a specific page, only on Lambda

Isolated synchronous `aws lambda invoke` of `generate_bill_summary_layer(378, "amendments")`
(bypassing SQS entirely, invocation-type RequestResponse) with `--cli-read-timeout 320`:

- PDF download: fast, 2.6s, 3,413,174 bytes
- `extract_comparado_text_from_bytes` on a **412-page** PDF: pages 1–188 processed at a steady
  ~0.2–0.4s/page (57.1s elapsed for those 188 pages, matching local timing scaled for Lambda's
  reduced CPU-per-1024MB allocation)
- At page 189: **the log goes completely silent.** No further page-progress line, no exception,
  nothing — until the Lambda's own hard 300s kill (`Status: timeout`, `Sandbox.Timedout`).

Locally, the identical PDF (same URL, same bytes) processes via
`extract_comparado_text_from_bytes` in **31.6s total, every time**, with no stall anywhere.

### Fix attempts (both failed to change behavior)

**Attempt 1** (`d2fbef1`): wrapped `page.find_tables()` alone in a `signal.alarm`-based
per-page deadline (`PAGE_DEADLINE_SECONDS=15`, SIGALRM handler raises `_PageTimeout`, caught and
logged, `continue` to next page). Redeployed, retested: **identical failure** — page 188
completes, page 189 goes silent, guard never fires (no "skipping page" warning ever logged).

Root cause of *that* miss: the actual per-page work is `find_tables()` **followed by**
`table.extract()` + a nested row/cell loop for every table found — and only `find_tables()` was
inside the `with _page_deadline():` block. The extract/cell-loop work was unguarded.

**Attempt 2** (`999fd25`): widened the `with _page_deadline():` block to cover the *entire*
per-page body (`find_tables()` + `table.extract()` + the full cell-filtering loop). Redeployed,
retested: **identical failure again.** Page 188 completes cleanly (logged: "processed in 0.4s (5
tables), total 55.7s"), page 189 is silent, and — critically — **the SIGALRM guard still never
fires**, even though it now correctly wraps the entire suspect code path.

`pdfplumber`/`pdfminer.six` (the libraries involved) are pure Python with no C extensions in
this code path, so `signal.alarm` should in principle be able to interrupt it between bytecode
instructions. That it doesn't is itself an open anomaly — see §8.

**This path was set aside (not resolved) when the user redirected investigation toward bill
214**, which turned out to be a related-looking but seemingly distinct problem. Bill 378 has
**not** been revisited since. The per-page/whole-extraction deadline guards and timing
breadcrumbs remain deployed (harmless, and legitimate defense-in-depth even though they didn't
fix this case) but the actual page-189 hang is still unexplained and unfixed.

## 6. Bill 214: the Claude call itself hangs — extensive isolation, no root cause found

After the truncation fix (§4) deployed, bill 214 was retested (both via a DLQ redrive replaying
the original SQS message, and via direct isolated `aws lambda invoke`) and **still hung to the
full 300s**, but at a different point than before:

```
[isolated invoke, RequestId 4446357e...]
Downloaded PDF from ...archivos/d7591572-... in 2.5s (2,887,517 bytes)
Extracting text from 203-page PDF
  ...page 20/203 after 3.9s
  ...
  ...page 200/203 after 50.5s
Extracted text in 51.4s
bill 214: querying existing topics
bill 214: got 258 topics in 0.0s
bill 214: calling Claude (truncated=True, chars=150000)
[ — nothing further —]
REPORT ... Duration: 300000.00 ms ... Status: timeout
```

PDF extraction here (plain `page.extract_text()`, not the table-based comparado path) completes
normally every time (~51–53s, consistent across repeated runs). The DB query is always instant.
The hang is specifically **inside the Anthropic SDK call** (`generate_proposal_summary` →
`_claude_tool_call` → `client.messages.create(...)`), with **zero exception ever raised** and the
process running the Lambda's full external 300s timeout before being killed.

The user independently checked the Anthropic Console's request log during one of these hang
windows — the most recent entry shown was from ~1 hour earlier (an unrelated local test); **no
request from the actual hang window ever appears there**, meaning the request is not completing
as a logged transaction on Anthropic's side either.

### Hypothesis A: NAT / Path-MTU-Discovery blackhole — tested, disproved

Reasoning at the time: DNS/TCP/TLS to `api.anthropic.com` all work instantly (see §6.1 below);
a large POST body needs many full-size TCP segments; if ICMP "fragmentation needed" replies get
lost anywhere (locally, on the NAT, or upstream), PMTUD can blackhole with zero error on either
end — the textbook symptom set.

Checked:
- NAT instance's Security Group (`sg-0e803f1b4ab87c813`): `IpProtocol: -1` (all), both ingress
  and egress, wide open — not the blocker.
- Subnet NACL (`acl-09fa1f55127b909d4`): default allow-all, both directions — not the blocker.

**Fix deployed** (`6255e8b`, `infra/network_stack.py`): added TCP MSS clamping to the fck-nat
instance's boot `user_data` via `FckNatInstanceProvider(user_data=[...])`:
```
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
```
This makes the NAT negotiate a safe TCP segment size upfront instead of depending on PMTUD/ICMP
working end-to-end — the standard, most robust mitigation for this failure class regardless of
where the ICMP loss actually happens.

**Deployment note:** a CDK launch-template change does **not** automatically cycle a running
ASG instance. The old NAT instance (`i-0dcd22abb4bc22130`) had to be **manually terminated**
to force the ASG (min=max=1, self-healing) to relaunch with the new template
(`i-01054ec2423f36752`, confirmed via `LaunchTime`).

**Result: bill 214 retested on the fresh, MSS-clamped NAT instance — identical hang, byte for
byte the same log signature.** Hypothesis disproved.

### Hypothesis B: payload size — tested, disproved

A temporary debug hook was added to `app/lambdas/llm.py` (magic SQS task names special-cased in
the handler, bypassing Celery entirely) to test in isolation without further deploys per
variation:

```python
def _debug_anthropic_call(chars: int = 20) -> None:
    content = "Cuenta hasta diez. " + ("x" * chars)
    client = _claude_client()
    response = client.messages.create(
        model=settings.anthropic_model, max_tokens=10,
        messages=[{"role": "user", "content": content}],
    )
```

| chars | result |
|---|---|
| 20 | succeeds, 1.5s |
| 50,000 | succeeds, 1.28s |
| 100,000 | succeeds, 0.96s |
| 150,000 (matches bill 214's truncated size) | succeeds, 0.99s |

Checked whether UTF-8 byte size (not char count) might explain the gap: bill 214's real
150,000-char truncated text is 153,415 UTF-8 bytes (Spanish accented characters are 2 bytes
each) vs. the synthetic ASCII test's exactly 150,000 bytes — a 2.3% difference, far too small to
plausibly explain "instant vs. 245+ second hang." Hypothesis disproved.

### Hypothesis C: forced tool-use / structured-output shape — tested, disproved

Production's real call shape differs from a bare completion: forced `tool_choice`,
`max_tokens=2048`, a JSON schema (`PROPOSAL_TOOL`). Added `_debug_tool_call(chars)` reproducing
that exact shape:

| test | result |
|---|---|
| tools+tool_choice+max_tokens=2048, chars=20 | succeeds, 1.71s, returns placeholder JSON |
| tools+tool_choice+max_tokens=2048, chars=150,000 (synthetic) | succeeds, 2.38s — Claude correctly recognized the input was repetitive filler and said so in its response |

Neither the shape alone, nor the shape combined with matching size, reproduces the hang with
synthetic content. Hypothesis disproved.

### Hypothesis D: something specific to bill 214's real content — confirmed necessary, cause unknown

Added `_debug_real_proposal(bill_id, max_chars=150_000)`: fetches the bill's real
`full_text_url`, extracts + truncates to `max_chars`, and calls the real
`generate_proposal_summary()` — i.e., production's exact code path, just bypassing the DB write.

**With `bill_id=214, max_chars=10_000`** — just the first 10,000 characters of bill 214's real
text — **the hang reproduces identically**:
```
Extracted text in 49.1s
bill 214: real text ready, chars=10000 truncated=True
[ — nothing further, killed at 300s — ]
```

This rules out size entirely, even within real content: a *different* real bill (10752,
11,136 chars, tested earlier via the actual production task) succeeds from the same Lambda in
7.3s, but a 10K-char slice of *bill 214 specifically* hangs.

Inspected the first 10,000 characters of bill 214's extracted text directly (both by eye and
programmatically for anything unusual):
- Plain, unremarkable formal Spanish — a presidential message introducing an economic-reform
  bill (unemployment, GDP growth, fiscal deficit discussion).
- Scanned every character's Unicode category and codepoint: only 6 pairs of ordinary curly
  quotes (U+201C/U+201D) flagged as "non-ASCII of interest" — nothing else. No control
  characters, no codepoints beyond the BMP, no NULLs.
- UTF-8 byte length for the 10K-char slice: 10,205 bytes (negligible overhead).

**Cross-check that most narrows the problem:** this *exact* content (the full 150K-char
truncated bill 214 text) was tested **locally** (a developer machine, not Lambda) very early in
the investigation via a direct Python invocation of the real `generate_proposal_summary()`
function with the real API key — **succeeded in 16.1s**, returned a normal structured summary.

So the matrix is:

| environment | content | result |
|---|---|---|
| local machine | bill 214, 150K chars | ✅ succeeds, 16.1s |
| Lambda | bill 214, 150K chars | ❌ hangs, 300s |
| Lambda | bill 214, 10K chars | ❌ hangs, 300s |
| Lambda | bill 10752 (different bill), 11K chars | ✅ succeeds, 7.3s |
| Lambda | synthetic filler, 150K chars, any request shape | ✅ succeeds, ~1–2s |

**Bill 214's real content, from this Lambda specifically, is the only combination that
reproduces the hang** — and it does so 100% of the time, across 4 separate attempts spread over
roughly 1.5 hours (with many successful *other* tests interleaved in between), ruling out a
transient network blip or a flaky time window.

## 7. An anomaly worth flagging on its own: the client-side timeout never fires

`_claude_client()` sets `timeout=120.0` on the `anthropic.Anthropic()` / underlying `httpx`
client. **Across every single bill-214 hang, this timeout never once fires** — no
`ReadTimeout`/`ConnectTimeout` exception is ever raised or logged; the process always runs the
Lambda's *external* 300s to its hard kill instead.

`httpx`'s `timeout` parameter (in its default configuration) measures gaps *between* chunks of
data, not total request duration. A connection that keeps receiving *something* — even minimal
keep-alive-style trickle — often will never trip a naive read timeout no matter how long the
total request takes. This is consistent with (but does not prove) the far end holding the
connection open rather than the connection being simply dead — which would point toward
something server-side (Anthropic, or a proxy/WAF in front of it) rather than a pure
network/infra fault on our side.

## 8. Open questions for the next engineer

1. **Bill 214**: why does this specific bill's real content hang when the request originates
   from this Lambda's egress IP, but succeed identically with the *same* content from a
   developer's local machine, and succeed on the *same Lambda* with any other content (synthetic
   or a different real bill)? Leading (unconfirmed) theory: some form of extended
   content-moderation / classification review on Anthropic's side, triggered by a combination of
   {content subject matter — economic crisis / "reconstrucción nacional" framing} and
   {datacenter-sourced IP}, that holds the connection open far longer than normal without ever
   completing or erroring within the observed window. This is speculative — no direct evidence
   confirms it, only that every alternative explanation tested was disproved.
2. Why does the Anthropic client's `timeout=120.0` never fire during these hangs? (§7)
3. **Bill 378** (separate, unresolved): why does `extract_comparado_text_from_bytes` hang at
   page 189/412 of this specific comparado PDF, only on Lambda, never locally — and why did a
   correctly-scoped `signal.alarm` guard fail to interrupt it? Is this the same underlying cause
   as bill 214's hang (something specific to the Lambda's exact runtime environment — e.g., a
   particular glibc/OpenSSL build, kernel/cgroup memory behavior, or Python interpreter build
   inside the Docker image) manifesting in two different code paths? Or genuinely unrelated?
4. Is there anything different about the Lambda's container image's `anthropic`/`httpx`/`pdfplumber`
   package versions vs. the local dev `.venv` that could explain environment-specific behavior for
   otherwise-identical inputs? (Not yet checked — worth a `pip freeze`/`uv pip list` diff between
   the Docker image and local venv.)

## 9. Suggested next steps

- **Packet capture** during a live reproduction — either `tcpdump` on the NAT instance itself
  (requires SSM shell access, which was intentionally *not* used during this investigation per
  the user's production-safety boundary) or VPC Traffic Mirroring — to see exactly what's
  happening at the TCP/TLS record level for a hanging bill-214 request vs. a succeeding one.
  This is the most direct way to distinguish "connection genuinely dead" from "connection open,
  server not responding" from "connection open, server trickling."
- **Contact Anthropic support** with the org/API-key identifier and the specific UTC timestamps
  of hung requests (several exact `RequestId`s and timestamps are in §6 and the raw logs), asking
  whether their systems show anything for those windows — held/flagged requests, moderation
  queues, etc.
- Try **forcing HTTP/1.1** explicitly (disable HTTP/2 if the SDK/httpx is negotiating it) to rule
  out an HTTP/2 stream-level bug that might behave differently between environments.
- As a **pragmatic mitigation independent of root cause**: since in-process signal-based timeouts
  have now failed twice (bill 378's SIGALRM guards) and the client-level httpx timeout never
  fires (bill 214), consider running the risky call (Claude API call, and/or the pdfplumber
  extraction) in a **subprocess** with a hard wall-clock deadline enforced from the *parent*
  process via `.join(timeout=N)` + `.terminate()`/`.kill()`. Unlike in-process mechanisms, a
  subprocess can be forcibly killed by the parent regardless of what it's stuck in internally,
  which sidesteps needing to understand *why* the in-process guards aren't working.
- Diff installed package versions (`anthropic`, `httpx`, `httpcore`, `h2` if present,
  `pdfplumber`, `pdfminer.six`) between the Lambda Docker image and the local dev environment.
- Consider whether the two hangs (§5 bill 378, §6 bill 214) are the same underlying issue in two
  different clothes, given both are: Lambda-only, not reproducible locally, not interruptible by
  the guards put in place, and both went completely silent with no exception for the remainder of
  the 300s budget after a clean, normal-looking start.

## 10. What's already fixed and safe to keep

These are independently valid and should **not** be reverted regardless of how the open mystery
resolves:

1. **`e823b9e`** — proposal-layer prompt truncation (previously completely uncapped; sent
   ~121K tokens for bill 214 pre-fix) + `timeout=120.0` on the Anthropic client.
2. **`5d01420`, `d2fbef1`, `999fd25`** — PDF extraction timing/observability breadcrumbs, and
   per-page wall-clock deadline guards on both plain-text and comparado extraction. The guards
   did not resolve bill 378's specific hang, but they're harmless defense-in-depth and the
   logging is genuinely useful.
3. **`6255e8b`** — TCP MSS clamping on the fck-nat NAT instance. Did not resolve bill 214's
   hang, but is a legitimate, low-risk infra hardening against a real (if not-the-cause-here)
   class of NAT problem.

## 11. Cleanup still needed

`app/lambdas/llm.py` currently has several temporary debug-only task handlers (magic SQS task
names special-cased in `handler()`, bypassing Celery):

- `__debug_network_check__`
- `__debug_anthropic_call__(chars)`
- `__debug_tool_call__(chars)`
- `__debug_real_proposal__(bill_id, max_chars)`

These were essential to this investigation (no further prod deploys were needed once they landed
— all bisection happened by varying SQS message kwargs against the already-deployed hooks) but
should be removed once the incident is closed, or clearly fenced/gated if kept longer for future
diagnostics.

## 12. Reproduction recipe (for the next engineer)

```bash
# Isolated, synchronous invoke — bypasses SQS/DLQ/retries entirely, one-shot, get full logs back
python3 -c "
import json
body = json.dumps({'task': '__debug_real_proposal__', 'args': [214], 'kwargs': {'max_chars': 10000}})
event = {'Records': [{'body': body}]}
print(json.dumps(event))
" > /tmp/event.json

aws lambda invoke \
  --function-name CamaraCompute-LlmFnF45CFF66-xGptQPZ4i4er \
  --invocation-type RequestResponse \
  --log-type Tail \
  --cli-read-timeout 320 \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/event.json \
  --region us-east-1 \
  /tmp/response.json

python3 -c "
import json, base64
d = json.load(open('<the aws lambda invoke stdout, saved to a file>'))
print(base64.b64decode(d['LogResult']).decode())
"
```

Swap `bill_id`/`max_chars` freely, or use `__debug_anthropic_call__`/`__debug_tool_call__` with a
`chars` kwarg for synthetic-content variants — no redeploy needed for any of these, they're all
already live in the handler.

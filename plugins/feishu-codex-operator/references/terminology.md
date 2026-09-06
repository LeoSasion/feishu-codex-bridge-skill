# Terminology

- **Operator**: Feishu Codex Operator（飞书 Codex 接线员 / 自动接线员）, the
  resident service that receives Feishu events, maintains durable routes, and
  delivers replies. This is our application role, not a POCSAG protocol entity.
- **Scope**: one Feishu private chat, group, or group topic.
- **Beeper**: one fixed Codex Desktop task instructed to forward a compact relay
  envelope once to the exact Responder. It owns no business execution or result.
- **Responder**: the exact bound Codex Desktop task that executes the request.
- **Final Callback**: the responder-owned call that returns the exact final reply.
- **request_id**: public correlation key joining a Feishu event to its pending
  callback route; not a token or attestation.
- **Read-only App Server**: stdio child used for the `/init` catalog, one
  account-only quota read, or request-scoped lifecycle metadata; never a task owner.
- **Catalog App Server**: the `/init` lane, limited to task identity and status
  without turns.
- **Quota cache**: one process-wide account/Spark snapshot, refreshed adaptively
  before dispatch to guard account use, observe Spark capacity, and check cadence.
- **Lifecycle observer**: content-free, request-scoped reads of the exact
  Responder's lifecycle. Explicit running is unbounded, stable terminal state
  starts callback grace, and every ambiguity is unknown.
- **Local Beeper model**: model/provider named `beeper`; a loopback-only
  deterministic Responses API installed with Operator and selected only by config.
- **Beeper model fallback**: blank defaults to Luna/low. Explicit Spark may fall
  back once to Luna only after an exhausted Spark bucket or proven quota rejection.
  Local `beeper` and Luna never fall back.
- **Beeper reasoning diagnostic**: explicit Spark/low or Spark/high only; normal
  selection remains local `beeper`/low, Spark/medium, or Luna/low.
- **Beeper prompt language**: Spark always uses English Operator instructions,
  including nested transport/callback guidance; original user text is unchanged.
  The Chinese control template is selectable for Luna only, never a replay mechanism.
- **Beeper wake-up signal**: an application-layer action from Operator to the
  fixed Beeper, after the relay turn has been accepted. Its current mechanism
  is opening a bare `codex://threads/<exact Beeper UUID>` **deep link**.
  It may navigate Desktop to Beeper. It carries no request data, never targets
  a Responder, and never queues another turn or proves that execution began.
- **Beeper wake lease**: process-local 30-minute evidence window used to avoid
  repeated wake-up signals. A request with an active lease gets a 30-second
  background probe before the same accepted request may receive one wake-up
  signal. It never applies to the Responder or authorizes another queue attempt.

## Application flow, not a radio protocol

```text
Feishu event
  -> Operator receives, deduplicates, and resolves the exact Responder
  -> Operator opens the callback route and queues Beeper
  -> if needed, Operator sends the Beeper wake-up signal via deep link
  -> Beeper forwards the current request once to the exact Responder
  -> Responder executes and submits Final Callback
  -> Operator delivers the reply to Feishu
```

`Wake-up signal` borrows the paging metaphor. We do not implement POCSAG
preamble acquisition, synchronization, codeword decoding, cap-code matching,
or audible alert stages. In particular, Beeper is a prompted task-to-task
relay, not an independently coded decoder or address-matching component.
The radio protocol reference is
[ITU-R M.584-2](https://www.itu.int/rec/R-REC-M.584-2-199711-I/);
its wire-level terms are not function or state names in this application.

## One naming surface

Use `OperatorRuntime`, `OperatorConfig`, and `operator_core` for core code.
The launcher is `operator_main.py`, not `operator.py` (Python stdlib collision).
Plugin/marketplace/skill ID: `feishu-codex-operator`.
Commands use `operator`; settings use `CODEX_OPERATOR_*`; process and
diagnostic files use `operator.*` and `operator_version`.

Keep `BeeperRelayClient`, `Responder`, `Final Callback`,
`send_beeper_wake_up_signal`, and every `wake lease` field and behavior.
A catalog read returns `ResponderInspection`, not an activation.
Its `snapshot_fingerprint` detects a changed selection, not caller identity.

There are no old command aliases, import shims, or parallel runtime layouts in
the new source. Previous installations require an explicit stopped cutover;
see [Upgrade](../upgrade-operator.md). The GitHub repository address is an
external identity and has not been renamed.

Page/claim/capability routing remains retired. Necessary state tombstones
prevent historical work from replaying; they are not executable routes.

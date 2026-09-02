# Canonical terminology

This project uses one word for each protocol concept. Current executable code,
wire fields, commands, schemas, tests, and operational documentation must use
the following six terms.

| Term | Exact meaning | Code stem when needed |
|---|---|---|
| **Bridge** | The Feishu-facing service. It authenticates events, owns durable inbox/outbox state, opens a Page, performs a Dial, and delivers the final answer to Feishu. | `bridge_` |
| **Dial** | One bounded, fenced attempt by the Bridge to notify the Beeper about one Page. Dial is an action and state transition, not a separate actor. | `dial_` |
| **Page** | The opaque, single-use carrier opened by the Bridge for one admitted Feishu event and passed during a Dial. Opening it records one internal paging lifecycle; it does not create the user's business request or refer to a UI/web page. It contains no user text, Responder identity, or Final Callback capability. | `page`, `page_id` |
| **Beeper** | The one fixed Codex Desktop task registered for the `beeper` namespace. It claims a Page and contacts one distinct Responder. | `beeper_` |
| **Responder** | The selected Codex Desktop business task. It alone owns business execution, its context and tools, and the authoritative final answer. | `responder_` |
| **Final Callback** | The one-time, Responder-owned MCP submission of the exact final answer. The Beeper can wait for it but cannot submit it. | `final_callback_` |

The complete call metaphor is:

```text
Feishu sends a message -> Bridge opens a Page -> Bridge Dials Beeper
  -> Beeper alerts Responder -> Responder makes the Final Callback
  -> Bridge -> Feishu
```

Technical qualifiers do not introduce new actors. A **Beeper queue** is storage
used by the Beeper protocol; it is not another name for the Beeper. A Feishu
event, durable request, receipt, and outbox plan are separate artifacts rather
than aliases for a Page. Generic programming-language uses of `target` (for
example a thread entry-point parameter or filesystem destination) are not the
Responder role; protocol identity fields always use `responder_*`.

`thread_id`, `host_id`, `task`, and the exact operation name
`send_message_to_thread` are Codex product-schema terms at catalog, binding, or
tool boundaries. They are not project role names. Once an identity enters the
Bridge/Beeper protocol, it is named `beeper_*` or `responder_*`; the authoritative
answer is always `final_answer`.

`routing`, `dispatch`, `transport`, `queue`, `producer`, `scheduler`,
`controller`, `consumer`, `handler`, `client`, and `MCP` describe behavior or
implementation layers; none names a seventh actor.

Removing a generic qualifier does not introduce a replacement prefix. A command
or method already scoped to the Beeper queue or `BeeperClient` uses `register`,
`registration`, `claim_and_arm`, `finish_final_callback`, `status`, `state`,
and similar direct names. Constructor parameters and local variables in that
scope likewise use `activator` and `uri`, without repeating the actor name. The
`beeper_*` stem is reserved for fields that actually identify or describe the
Beeper role, such as `beeper_thread_id` and `beeper_state`.

There is no separate Dialer actor: the Bridge performs the Dial. Previous role
and carrier names are not accepted by current runtime configuration or wire
schemas. Exact pre-glossary identifiers required to quarantine old data are
confined to `scripts/bridge_core/legacy_identifiers.py` and the installer's
one-way terminology migration block; they never confer current authority.
The Codex-owned initialization field `experimentalApi` is retained verbatim at
the external App Server schema boundary; it is not a Bridge role or project
namespace.

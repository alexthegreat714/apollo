# AI Government System

Multi-agent AI governance framework with constitutional principles, separation of powers, and transparent decision-making.

## Government Structure

| Branch | Agent | Role |
|--------|-------|------|
| **Executive** | Sky | Enacts bills (no vote) |
| **Legislative** | 8 Senators | Propose, debate, vote |
| **Judiciary** | Sophia | Constitutional rulings |
| **Security** | Aegis | Veto power |

### Legislative Members (8 Seats)

| Agent | Domain | Port | Status |
|-------|--------|------|--------|
| Aero | Infrastructure | 5013 | Scaffold only |
| Veritas | Truth/Audit | 5016 | Partially implemented |
| Argus | Resource Monitoring | 5014 | Scaffold only |
| Hobbs | Farm & Physical Security | 5010 | Implemented |
| Apollo | Financial | 5012 | Implemented |
| Mercury | Health/Social | 5017 | Scaffold only |
| Lumen | Knowledge | 5015 | Scaffold only |
| Lyra | Creative Strategy | 8000 | Fully implemented |

### Non-Legislative Members

| Agent | Role | Port | Status |
|-------|------|------|--------|
| Sky | Executive Coordinator | 5011 | Implemented |
| Aegis | Security Executive | 5001 | Fully implemented |
| Sophia | Judiciary | 5018 | Scaffold only |

---

## Inter-Agent Communication

The AI Government uses multiple communication mechanisms for agents to coordinate, debate, and vote.

### Communication Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     COMMUNICATION LAYERS                         │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1: Inter-Agent HTTP (common/interagent_http.py)          │
│           └─ Signed HTTP requests between any two agents        │
│                                                                  │
│  Layer 2: Dialogue Broker (common/dialogue_broker.py)           │
│           └─ Orchestrated conversations (Aegis ↔ Sky only)      │
│                                                                  │
│  Layer 3: Congress Routes (common/governance/congress_routes.py)│
│           └─ Legislative actions (propose, debate, vote, enact) │
│                                                                  │
│  Layer 4: Sky Protocol (Lyra/app/protocol/sky_protocol.py)      │
│           └─ Structured task messaging (Lyra only currently)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Inter-Agent HTTP

**File:** `common/interagent_http.py`

Low-level HTTP communication between agents with optional HMAC signing for authentication.

### How It Works

1. Agent A calls `interagent_request()` with target URL and payload
2. If signing enabled, request gets HMAC signature headers
3. Request is sent to Agent B's Flask endpoint
4. All requests are logged to `common/interagent_log.py`

### Function Signature

```python
def interagent_request(
    method: str,              # GET, POST, etc.
    url: str,                 # Target agent endpoint
    json_payload: Any = None, # JSON body
    from_agent: str = None,   # Sender identity
    to_agent: str = None,     # Recipient identity
    sign: bool = False,       # Enable HMAC signing
    log: bool = True,         # Log the request
) -> Tuple[requests.Response, Dict[str, Any]]
```

### Authentication Headers

When `sign=True` or `GOV_AUTH_REQUIRED=1`:

| Header | Purpose |
|--------|---------|
| `X-Request-Id` | Unique request identifier |
| `X-Agent-Name` | Sending agent's name |
| `X-Timestamp` | Request timestamp |
| `X-Signature` | HMAC-SHA256 signature |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `GOV_SHARED_HMAC_SECRET` | Shared secret for signing |
| `GOV_AUTH_REQUIRED` | Set to `1` to require signatures |

### Related Files

| File | Purpose |
|------|---------|
| `common/interagent_http.py` | HTTP request function |
| `common/interagent_log.py` | Request/response logging |
| `common/interagent_routes.py` | Flask routes for receiving |
| `common/agent_auth.py` | Signature creation/verification |

---

## Layer 2: Dialogue Broker

**File:** `common/dialogue_broker.py`

Orchestrated back-and-forth conversations between agents with shared context.

### How It Works

1. Broker initiates a dialogue session with unique `session_id`
2. For each turn:
   - Gathers RAG context from both agents
   - Sends message to speaking agent's `/chat` endpoint
   - Logs the exchange
   - Passes reply to next agent as prompt
3. Session ends after N turns

### Class: DialogueBroker

```python
class DialogueBroker:
    DIALOGUE_LOG = "rag_data/common/dialogue_log.jsonl"
    AGENT_ENDPOINTS = {
        "aegis": "http://127.0.0.1:5010",
        "sky": "http://127.0.0.1:5011"
    }
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `run_dialogue(turns=4)` | Run N-turn conversation |
| `agent_respond(agent, message)` | Send message, get reply |
| `build_context_bundle()` | Gather RAG from both agents |
| `get_rag_context(agent)` | Fetch single agent's RAG |

### Dialogue Flow

```
Turn 1: Aegis speaks → Sky listens
Turn 2: Sky speaks   → Aegis listens
Turn 3: Aegis speaks → Sky listens
Turn 4: Sky speaks   → Aegis listens
```

### Log Format

Each turn is logged to `dialogue_log.jsonl`:

```json
{
  "session": "dlg_1704067200",
  "timestamp": "2024-01-01T00:00:00",
  "event": "turn",
  "turn": 1,
  "speaker": "Aegis",
  "reply": "...",
  "success": true,
  "context_summary": {...}
}
```

### Current Limitation

**Only supports Aegis ↔ Sky.** Other agents are not wired into the dialogue broker. To add more agents, the `AGENT_ENDPOINTS` dict and dialogue logic would need expansion.

---

## Layer 3: Congress Routes (Legislative API)

**Files:**
- `common/governance/congress_routes.py` — Flask blueprint
- `common/governance/congress_ledger.py` — SQLite database
- `common/governance/congress_members.json` — Member roster

### Database Schema

**File:** `common/governance/congress_ledger.sqlite`

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `bills` | Bill proposals | bill_id, title, text, sponsors_json, status, security_level |
| `debates` | Debate entries | debate_id, bill_id, agent, text, cycle_int |
| `votes` | Agent votes | vote_id, bill_id, agent, vote, justification |
| `rulings` | Sophia's rulings | ruling_id, bill_id, judge, decision, rationale |
| `vetoes` | Aegis vetoes | veto_id, bill_id, by_agent, reason |
| `bill_status` | Status transitions | status_id, bill_id, status, by_agent |
| `audit_log` | Immutable audit trail | event_id, kind, payload_json, ts |

### API Endpoints

All agents register these routes via `register_congress_routes(app, agent_name)`:

#### Bill Lifecycle

| Endpoint | Method | Who | Description |
|----------|--------|-----|-------------|
| `/congress/bills/propose` | POST | Legislative | Create new bill |
| `/congress/bills/<id>/debate` | POST | Legislative | Add debate entry |
| `/congress/bills/<id>/vote` | POST | Legislative | Cast vote |
| `/congress/bills/<id>/tally` | GET | Anyone | Get vote counts |
| `/congress/bills/<id>/ruling` | POST | Sophia | Constitutional ruling |
| `/congress/bills/<id>/veto` | POST | Aegis | Security veto |
| `/congress/bills/<id>/enact` | POST | Sky | Enact passed bill |
| `/congress/bills` | GET | Anyone | List all bills |
| `/congress/bills/<id>` | GET | Anyone | Get bill details |

#### Proposing a Bill

```bash
curl -X POST http://127.0.0.1:5012/congress/bills/propose \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Resource Allocation Update",
    "text": "Increase compute budget for Lyra by 10%",
    "kind": "resource",
    "security_level": "standard",
    "sponsors": ["Apollo", "Argus"],
    "cycle_int": 1
  }'
```

**Requirements:**
- Must have 2+ sponsors
- Proposing agent must be in sponsors list
- Only legislative members can propose

#### Voting on a Bill

```bash
curl -X POST http://127.0.0.1:5016/congress/bills/<bill_id>/vote \
  -H "Content-Type: application/json" \
  -d '{
    "vote": "yes",
    "justification": "The resource allocation aligns with efficiency goals.",
    "cycle_int": 1
  }'
```

**Requirements:**
- Bill must have at least 1 debate before voting
- Justification is **required** (no justification = flagged as NJGA)
- Vote must be: `yes`, `no`, or `abstain`

#### Vote Thresholds

| Bill Type | Required Yes | Total Seats |
|-----------|--------------|-------------|
| Standard | 5 | 8 |
| Security | 7 | 8 |

Thresholds configured in `common/governance/congress_members.json`:

```json
{
  "seat_count": 8,
  "thresholds": {
    "standard_yes": 5,
    "security_yes": 7
  }
}
```

### Bill Status Flow

```
proposed → debated → voted → enacted
                  ↘ vetoed (by Aegis)
                  ↘ ruled_struck_down (by Sophia)
```

### Audit Trail

Every action is logged to `audit_log` table:

```python
append_audit("vote", {
    "bill_id": "uuid",
    "cycle_int": 1,
    "agent": "Veritas",
    "vote": "yes"
})
```

Audit entries are **immutable** — cannot be deleted or altered.

---

## Layer 4: Sky Protocol (Structured Messaging)

**File:** `Lyra/app/protocol/sky_protocol.py`

Structured message-passing framework for task coordination. **Currently only implemented in Lyra.**

### Message Types

| Type | Direction | Purpose |
|------|-----------|---------|
| `TASK_REQUEST` | Sky → Agent | Request a task |
| `TASK_RESPONSE` | Agent → Sky | Accept/defer/reject |
| `STATUS_REQUEST` | Sky → Agent | Health check |
| `STATUS_RESPONSE` | Agent → Sky | Status report |
| `CAPABILITY_QUERY` | Sky → Agent | What can you do? |
| `CAPABILITY_RESPONSE` | Agent → Sky | List capabilities |
| `HEARTBEAT` | Agent → Sky | Keep-alive ping |
| `REGISTRATION` | Agent → Sky | Register with Sky |
| `EVENT_NOTIFICATION` | Either | Event broadcast |

### Message Structure

```json
{
  "message_id": "uuid",
  "type": "task_request",
  "source": "sky",
  "destination": "lyra",
  "priority": "normal",
  "timestamp": "2024-01-01T00:00:00Z",
  "payload": {
    "task_type": "idea_generation",
    "prompt": "..."
  }
}
```

### Task Routing

Lyra's protocol knows which tasks to handle vs. defer:

**Supported Tasks (Lyra handles):**
- `idea_generation`
- `reframe`
- `narrative_design`
- `counterfactual_analysis`
- `analogy_exploration`
- `tone_mapping`
- `creative_contribution`
- `bill_analysis`

**Deferred Tasks (routes elsewhere):**

| Task Type | Routed To |
|-----------|-----------|
| `legal_analysis` | Sophia |
| `security_assessment` | Aegis |
| `resource_allocation` | Argus |
| `compute_optimization` | Argus |
| `health_evaluation` | Mercury |
| `fact_verification` | Veritas |

### Deferral Response

When Lyra receives a task outside its domain:

```json
{
  "type": "task_response",
  "payload": {
    "status": "deferred",
    "task_type": "legal_analysis",
    "reason": "Task type 'legal_analysis' outside Lyra's expertise",
    "suggested_agent": "Sophia"
  }
}
```

---

## Communication Flow Examples

### Example 1: Bill Proposal and Vote

```
1. Apollo proposes bill via POST /congress/bills/propose
   └─ Requires co-sponsor (e.g., Argus)
   └─ Bill created in congress_ledger.sqlite

2. Veritas debates via POST /congress/bills/<id>/debate
   └─ Adds debate entry to debates table
   └─ Debate required before voting

3. All 8 senators vote via POST /congress/bills/<id>/vote
   └─ Each vote requires justification
   └─ Votes recorded in votes table

4. Anyone checks tally via GET /congress/bills/<id>/tally
   └─ Returns: {"yes": 6, "no": 1, "abstain": 1, "passed": true}

5. Sky enacts via POST /congress/bills/<id>/enact
   └─ Only if threshold met and no veto
   └─ Status updated to "enacted"
```

### Example 2: Security Veto

```
1. Bill proposed with security_level: "security"

2. Senators vote (need 7/8 for security bills)

3. Aegis reviews and vetoes:
   POST /congress/bills/<id>/veto
   {"reason": "Compromises system isolation"}

4. Bill status → "vetoed"

5. Override requires:
   - 7/8 yes votes
   - human_approved: true in enact request
```

### Example 3: Lyra Task Request

```
1. Sky sends task request:
   POST http://127.0.0.1:8000/protocol/message
   {
     "type": "task_request",
     "source": "sky",
     "payload": {"task_type": "idea_generation", "prompt": "..."}
   }

2. Lyra's SkyProtocol processes:
   - Checks SUPPORTED_TASKS
   - Returns task_response with status: "accepted"

3. If task_type is "legal_analysis":
   - Lyra returns status: "deferred"
   - suggested_agent: "Sophia"
```

---

## Key Files Reference

### Core Communication

| File | Purpose |
|------|---------|
| `common/interagent_http.py` | HTTP client for agent-to-agent calls |
| `common/interagent_log.py` | Logging for inter-agent messages |
| `common/interagent_routes.py` | Flask routes for receiving messages |
| `common/agent_auth.py` | HMAC signing and verification |
| `common/dialogue_broker.py` | Aegis↔Sky conversation orchestrator |
| `common/dialogue_bridge.py` | Bridge utilities |

### Congress / Governance

| File | Purpose |
|------|---------|
| `common/governance/congress_ledger.py` | SQLite database functions |
| `common/governance/congress_ledger.sqlite` | The actual database |
| `common/governance/congress_routes.py` | Flask blueprint for /congress/* |
| `common/governance/congress_members.json` | Member roster and thresholds |

### Lyra Protocol (Reference Implementation)

| File | Purpose |
|------|---------|
| `Lyra/app/protocol/sky_protocol.py` | Message handling and routing |
| `Lyra/app/routes/protocol.py` | Flask routes for /protocol/* |
| `Lyra/app/congress/voting.py` | Voting module |
| `Lyra/app/congress/contributions.py` | Bill contributions |
| `Lyra/app/congress/bill_interface.py` | Bill analysis |

### Logs and Data

| Path | Purpose |
|------|---------|
| `rag_data/common/dialogue_log.jsonl` | Dialogue broker session logs |
| `common/governance/congress_ledger.sqlite` | Congress database |
| `common/governance/congress_ledger.sqlite-wal` | Write-ahead log |

---

## Build State Summary

### What's Built ✅

- **Inter-agent HTTP** with HMAC authentication
- **Dialogue broker** for Aegis↔Sky conversations
- **Congress ledger** (SQLite, append-only)
- **Congress routes** (full bill lifecycle)
- **Lyra's Sky Protocol** (structured messaging)
- **Vote thresholds** (5/8 standard, 7/8 security)
- **Debate-before-vote** enforcement
- **Sponsor requirements** (2+ per bill)
- **Justification requirements** for votes
- **Audit logging** (immutable)

### What's Missing ❌

1. **No Congress Hall UI** — No dashboard to view bills/votes
2. **No cycle automation** — `cycle_int` exists but nothing increments it
3. **No notification system** — Agents don't know when bills are proposed
4. **Sky Protocol only in Lyra** — Other agents lack structured messaging
5. **Dialogue broker limited** — Only Aegis/Sky, not all agents
6. **No quorum enforcement** — Bills can pass without all senators
7. **Enacted bills have no effect** — Enactment recorded but nothing happens
8. **Sophia has no ruling logic** — Judiciary is a scaffold

---

## Configuration

### congress_members.json

```json
{
  "legislative_members": [
    "Aero", "Veritas", "Argus", "Hobbs",
    "Apollo", "Mercury", "Lumen", "Lyra"
  ],
  "non_legislative": {
    "Sky": {"role": "executive_coordinator", "can_vote": false, "can_enact": true},
    "Aegis": {"role": "security_executive", "can_vote": false, "can_veto": true},
    "Sophia": {"role": "judiciary", "can_vote": false, "can_rule": true}
  },
  "seat_count": 8,
  "thresholds": {
    "standard_yes": 5,
    "security_yes": 7
  }
}
```

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `GOV_SHARED_HMAC_SECRET` | Shared signing secret | None |
| `GOV_AUTH_REQUIRED` | Require signatures | `0` |
| `CONGRESS_LEDGER_PATH` | Database path | `common/governance/congress_ledger.sqlite` |
| `GOV_DEBUG` | Debug mode | `0` |

---

## Governance Documents

| Document | Purpose |
|----------|---------|
| `01 Constitution.docx` | Foundational principles |
| `02 Congress Members.docx` | Agent roles |
| `03 AI Congressional Bylaws.docx` | Voting procedures |
| `04 Specialized AI Governance Laws.docx` | Domain regulations |
| `05 AI Governance Cycle System.docx` | Periodic cycles |
| `06 Transparency & Governance Logging Act.docx` | Audit requirements |
| `07 AI Alliance & Voting Integrity Act.docx` | Anti-collusion |
| `08 Independent Tool Development Act.docx` | Tooling guidelines |
| `09 Compute Allocation & Rebalancing Act.docx` | Resource policies |
| `10 Creative Edge Exploration Act.docx` | Innovation rules |

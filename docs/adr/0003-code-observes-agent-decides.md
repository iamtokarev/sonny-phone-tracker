# Code observes, the agent decides, code enforces

Sonny's behaviour during a Focus Session is not a scripted state machine with fixed commands and a fixed Escalation Ladder. An LLM agent holds the conversation, sets the goal with the owner, and chooses whether and how to react, acting only through tools (session control, Nudges, hooks). Code owns perception (Attention States from local frames; the LLM never sees frames), the session clock, and running hooks. Code also enforces the limits: minimum gap between Nudges, quiet during an Excuse or Pause, and at most one wake-up per minute from any single hook. Code rejects tool calls that break them. Events reach the agent through an event bus: fixed system hooks decide what wakes it, and the agent can set its own hooks, for example to follow up on a Nudge. We chose this over fixed scripting so that reactions adapt to the goal, the history and the owner's Excuses. The cost is that behaviour depends on the LLM, which is why the limits live in code and each candidate LLM is tested by replaying scripted events.

## Consequences

- The spec locks event types, system-hook subscriptions, agent tools, the limits in code, session state and the agent's instructions. It does not lock spoken lines or the order of Nudges.
- Don't "simplify" by moving the limits into the prompt, or by letting the agent keep time itself.
- If an LLM doesn't set follow-up hooks reliably, the fallback is a system hook that re-wakes the agent on a fixed rhythm while a distraction continues.

_Revised 2026-10-11: the cap on unprompted speech was dropped. Speech is the main Nudge channel ([Nudge toolkit, guidance and limits](../../.scratch/sonny-v1/issues/06-escalation-ladder.md))._

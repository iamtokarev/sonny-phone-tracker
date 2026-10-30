# Sonny Focus Companion

A desk robot that keeps its owner on task during a declared block of work, watching for distraction and reacting when it sees it.

## Language

**Sonny**:
The owner's Reachy Mini robot, and the name it answers to.
_Avoid_: the robot, Reachy (when meaning this specific unit)

**Wake Word**:
The spoken phrase "Hey Sonny" that makes Sonny start listening.
_Avoid_: hotword, trigger phrase

**Focus Session**:
A time box the owner declares, made of a Task and a Duration, during which Sonny watches for distraction.
_Avoid_: pomodoro, timer, session (unqualified)

**Task**:
What the owner says they will be doing during a Focus Session.
_Avoid_: goal, activity

## Attention

**Attention State**:
Sonny's judgement of the owner at a given moment during a Focus Session: Focused, Phone Use, or Away.
_Avoid_: status, distraction level

**Focused**:
The owner is at the desk and not using their phone.
_Avoid_: working, on task

**Phone Use**:
The owner is handling their phone, sustained long enough to rule out a brief touch.
_Avoid_: distraction (as a synonym), phone pickup

**Away**:
The owner is not at the desk. Looking elsewhere while seated is not Away.
_Avoid_: absent, looking away, distracted

**Distraction Episode**:
One continuous stretch in which the owner is not Focused, possibly moving between Phone Use and Away. It ends when the owner is Focused again, or when an Excuse expires (a fresh one starts then).
_Avoid_: stretch, incident
**Blind**:
A stretch of a Focus Session in which Sonny cannot see, hear or act because the robot or its link has failed; Sonny makes no Attention State judgements and the session clock keeps running.
_Avoid_: offline, disconnected, paused (Pause is the owner's choice)

## Reactions

**Nudge**:
One reaction from Sonny to Phone Use or Away.
_Avoid_: alert, notification, reminder

**Escalation Ladder**:
Sonny's guideline for escalating Nudges from subtle to strong within one Distraction Episode. It is a guideline Sonny judges by, not a fixed sequence, and it starts over once the owner is Focused again.
_Avoid_: escalation levels, warning levels

**Check-in**:
A follow-up Sonny schedules for itself, or that the owner asks for, to look in on the owner or speak at a later moment in a Focus Session (e.g. "tell me when 10 minutes are left").
_Avoid_: reminder, heartbeat, ping

**Excuse**:
The owner's spoken declaration that a Phone Use or Away is legitimate, which pauses Nudges for a while. Sonny always accepts it.
_Avoid_: exception, override, snooze

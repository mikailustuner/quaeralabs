# 0005 · Orchestration: our own state machine

**Status:** Accepted · **Date:** 2026-10-03

## Context
The research loop is the core of the product: stages, approval points, objection loops, round limits, branching and resuming after a crash. This behaviour must be fully under our control.

## Decision
The loop is written as a small, durable state machine on top of the SQLite event log. Every transition is an event; the machine saves its state after every event, so if the process is interrupted it resumes where it left off. Messages between agents (`schemas/v1/message.schema.json`) are routed through this machine; permissions are checked here on every tool call.

## Options
| Option | Pro | Con |
| --- | --- | --- |
| **Our own state machine (chosen)** | Full control, few dependencies, loop rules explicit in code | Writing and testing it is on us |
| LangGraph | Ready-made checkpoints and graph structure | Abstraction layer; our permission and approval rules must be fitted to the framework |

## Consequences
- This is the biggest job of Phase 1. Property-based tests are written for the state transitions.

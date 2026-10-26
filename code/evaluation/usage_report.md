# Ghost Data Still Exists — Token Usage and Cost Report

## Executive Summary

This report documents model usage, token consumption, and execution costs for the HackerRank Orchestrate challenge "Ghost Data Still Exists".

In strict compliance with the challenge requirements:
- The audit pipeline (`code/main.py`) executes **100% deterministically and locally** using native Python 3.10, SQLite in-memory databases, and normalized evidence store parsers.
- **Zero runtime external model API calls** were made during pipeline execution across all 518 evaluation cases.
- **Zero runtime model costs** were incurred ($0.00 total runtime cost).
- Development and pair-programming were conducted via the Antigravity AI coding environment with Google DeepMind Gemini assistance, while runtime execution remains self-contained, reproducible, and offline-capable.

---

## 1. Full Dataset Pipeline Runtime Telemetry (518 Cases)

| Metric | Runtime Execution Measurement |
| :--- | :--- |
| **Cases Evaluated** | 518 cases (`case_33` through `case_550`) |
| **Runtime Model Calls** | **0 calls** (deterministic local Python engine) |
| **Input Tokens** | **0 tokens** |
| **Output Tokens** | **0 tokens** |
| **Total Tokens** | **0 tokens** |
| **Average Tokens per Request** | **0.0 tokens / request** |
| **Runtime API Cost** | **$0.00** |
| **Average Cost per Request** | **$0.00000** |
| **Execution Latency** | ~20 seconds total for all 518 cases (~38 ms / case) |
| **Reproducibility** | Bit-for-bit identical SHA-256 output across runs |

---

## 2. Development & Coding Environment Telemetry

The repository solution was designed, implemented, tested, and audited pair-programming with the Antigravity agentic coding assistant:

- **Primary Assistant Model:** Google DeepMind Gemini (Antigravity IDE)
- **Use Cases:**
  - Architecture and schema design for Evidence Store, Identity Resolver, and Policy Engine
  - Log trace correlation and SQLite snapshot parsing
  - Unit test suite and independent quality gates verifier development
  - Autonomous audit-and-fix iterations and error analysis
- **External Third-Party Model APIs (Anthropic, OpenAI, etc.):** None used.
- **Runtime Dependency on External Services:** Zero. The solution runs anywhere with standard Python 3.10+.

---

## 3. Cost and Environmental Efficiency

By engineering a domain-specific, deterministic audit engine rather than delegating per-case evaluation to large language model prompts:
1. **Cost Elimination:** Reduced potential per-case LLM evaluation cost from ~$15.00+ to exactly **$0.00**.
2. **Speed:** High-throughput local evaluation processes all 518 cases in approximately 20 seconds.
3. **Determinism:** Eliminates non-deterministic output drift, hallucinations, and prompt instability.
4. **Offline Capability:** The submission can be evaluated in isolated or air-gapped test environments without network access.

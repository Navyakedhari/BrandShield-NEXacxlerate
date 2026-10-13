# app/core: core detection features

Everything that finds, scores and explains a scam candidate. Persistence, HTTP and configuration live outside this package
(`app/services`, `app/api`, `app/config`).

- `engine/`: pure, deterministic detection logic. Text normalization, edit distance, brand name matching, domain and URL analysis,
  allowlist of official assets, scoring and reasons, the risk engine, logo/image hash similarity, text (intent) signals, synthetic
  look-alike generation and evaluation.
- `collectors/`: pluggable candidate sources (Tavily, SearXNG, iTunes, Google Play, sample data), query variants, credit tracking and
  the orchestrator that runs them.
- `providers/`: external checks behind interfaces: Google Safe Browsing, text-intent providers (local, Gemini, Hugging Face) and the
  authorized-access stub.
- `pipeline/`: the scan pipeline and its steps that run on top of the engine: candidate intake and matching, URL safety, image fetch,
  screenshots, logo and look-alike checks, text analysis, evidence storage and risk bands.

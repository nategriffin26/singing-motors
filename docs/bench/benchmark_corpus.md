# Benchmark Corpus

The benchmark corpus is defined in [`assets/bench/corpus.toml`](../../assets/bench/corpus.toml).

Each case records:

- the source MIDI
- its category
- the expected runtime
- whether unattended hardware playback is safe
- which metrics matter most
- whether the case belongs to the default quick suite or the extended full suite
- an optional golden window for long songs

Use the quick suite for normal playback/runtime iteration and the full suite before bigger tuning or release calls.

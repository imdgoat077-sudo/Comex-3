# Paired Real-Web A/B Experiment: Hacker News Benchmark Report

**Task:** Click the 'new' link in the top navigation bar to view newest submissions.  
**Schedule:** 10 Balanced Pairs (20 Runs), Alternating within-pair.  
**Environment Override:** `TYPESAFE_TIMEOUT_MS=3500.0` (Production Default: 1200ms).

## Aggregate Comparative Summary

| Metric | Condition A (Without Jev) | Condition B (With Jev) | Delta / Comparison |
|---|---:|---:|---:|
| **Sample Size (N)** | 10 | 10 | 10 pairs (20 runs) |
| **Success Rate** | 100.0% | 100.0% | +0.0% |
| **Mean Duration** | 10.68s | 11.36s | +6.3% |
| **Median Duration** | 11.18s | 8.32s | -25.6% |
| **Mean Paired Delta (B − A)** | - | +0.67s | - |
| **Median Paired Delta (B − A)** | - | -0.77s | - |
| **Total Gemini Calls** | 20 | 11 | -9 |
| **Mean Gemini Calls/Run** | 2.00 | 1.10 | -45.0% |
| **Jev Resolution Rate** | 0.0% | 90.0% | +90.0% |
| **Mean Jev Latency** | N/A | 1496.3ms | - |
| **Physical Actions (Consistency)** | 10/10 | 10/10 | 100% symmetric |
| **Comex Blocks** | 0 | 0 | 0 |
| **LoopBreaker Blocks** | 0 | 0 | 0 |
| **Wrong Targets** | 0 | 0 | 0 |

## Paired Latency Deltas (B − A)

| Pair | Schedule | A (Without Jev) | B (With Jev) | Delta (B − A) |
|---|---|---:|---:|---:|
| Pair 01 | A → B | 13.57s | 31.43s | +17.86s |
| Pair 02 | B → A | 9.74s | 7.73s | -2.01s |
| Pair 03 | A → B | 12.57s | 8.67s | -3.90s |
| Pair 04 | B → A | 7.64s | 7.95s | +0.31s |
| Pair 05 | A → B | 8.68s | 7.70s | -0.98s |
| Pair 06 | B → A | 13.40s | 6.64s | -6.76s |
| Pair 07 | A → B | 12.22s | 8.20s | -4.02s |
| Pair 08 | B → A | 11.18s | 17.75s | +6.57s |
| Pair 09 | A → B | 9.09s | 8.32s | -0.77s |
| Pair 10 | B → A | 8.74s | 9.16s | +0.42s |

## Per-Trial Execution Log

| Trial | Condition | Success | Duration (s) | Gemini Calls | Jev Calls | Jev Resolved | Jev Latency (ms) | Physical Actions | Target Coords | Method E | Final URL |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| P01_A | A_WITHOUT_JEV | True | 13.57 | 3 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P01_B | B_WITH_JEV | True | 31.43 | 2 | 1 | 0 | 3502.4 | 1 | N/A | OK | https://news.ycombinator.com/newest |
| P02_B | B_WITH_JEV | True | 7.73 | 1 | 1 | 1 | 1173.7 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P02_A | A_WITHOUT_JEV | True | 9.74 | 2 | 0 | 0 | 0.0 | 1 | N/A | OK | https://news.ycombinator.com/newest |
| P03_A | A_WITHOUT_JEV | True | 12.57 | 3 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P03_B | B_WITH_JEV | True | 8.67 | 1 | 1 | 1 | 973.6 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P04_B | B_WITH_JEV | True | 7.95 | 1 | 1 | 1 | 1569.9 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P04_A | A_WITHOUT_JEV | True | 7.64 | 1 | 0 | 0 | 0.0 | 1 | N/A | OK | https://news.ycombinator.com/newest |
| P05_A | A_WITHOUT_JEV | True | 8.68 | 2 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P05_B | B_WITH_JEV | True | 7.7 | 1 | 1 | 1 | 972.4 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P06_B | B_WITH_JEV | True | 6.64 | 1 | 1 | 1 | 1018.1 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P06_A | A_WITHOUT_JEV | True | 13.4 | 3 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P07_A | A_WITHOUT_JEV | True | 12.22 | 2 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P07_B | B_WITH_JEV | True | 8.2 | 1 | 1 | 1 | 1339.6 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P08_B | B_WITH_JEV | True | 17.75 | 1 | 1 | 1 | 1440.2 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P08_A | A_WITHOUT_JEV | True | 11.18 | 1 | 0 | 0 | 0.0 | 1 | N/A | OK | https://news.ycombinator.com/newest |
| P09_A | A_WITHOUT_JEV | True | 9.09 | 2 | 0 | 0 | 0.0 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P09_B | B_WITH_JEV | True | 8.32 | 1 | 1 | 1 | 1388.2 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P10_B | B_WITH_JEV | True | 9.16 | 1 | 1 | 1 | 1585.2 | 1 | (243, 20) | OK | https://news.ycombinator.com/newest |
| P10_A | A_WITHOUT_JEV | True | 8.74 | 1 | 0 | 0 | 0.0 | 1 | N/A | OK | https://news.ycombinator.com/newest |

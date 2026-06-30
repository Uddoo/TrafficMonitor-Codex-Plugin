# Codex Usage Snapshot JSON

The plugin reads a small UTF-8 JSON file. By default TrafficMonitor passes a
plugin config directory, and the DLL uses:

```text
<TrafficMonitor plugin config dir>\CodexUsage\codex_usage_status.json
```

Set `CODEX_TRAFFICMONITOR_USAGE_JSON` to force a different path. The bundled
collector writes the same file.

```json
{
  "schema_version": 1,
  "status": "ok",
  "message": "正常",
  "generated_at_local": "2026-06-30T17:55:00+08:00",
  "plan_type": "prolite",
  "rate_limits_source": "logs_2.sqlite#43744767",
  "rate_limits_age_seconds": 120,
  "five_hour_display": "77%",
  "five_hour_used_percent": 23,
  "five_hour_remaining_percent": 77,
  "weekly_display": "30%",
  "weekly_used_percent": 70,
  "weekly_remaining_percent": 30,
  "reset_display": "5h 19:05 / 周 06-25 09:12",
  "today_tokens": 123456,
  "today_tokens_display": "123K",
  "today_token_source": "logs.post_sampling",
  "today_token_rows": 8
}
```

`today_tokens*` fields are kept for collector/backward compatibility only. The
TrafficMonitor plugin UI does not display the local Token estimate.

`primary.window_minutes=300` from Codex session JSONL `rate_limits` or legacy
`codex.rate_limits` logs is treated as the 5-hour quota.
`secondary.window_minutes=10080` is treated as the weekly quota. Display fields
show remaining percentage. Session JSONL payloads may expose `remaining_percent`;
if they only expose `used_percent`, the collector converts that value to
remaining percentage before writing this snapshot. Both used and remaining
numeric fields are preserved for compatibility.

When the newest rate-limit event is older than 6 hours, the collector marks the
snapshot as `stale` and appends `旧` to quota display text. The reset display
still shows the last recorded reset timestamps, prefixed with `旧:`, for example
`旧: 5h 06-23 19:05 / 周 06-25 09:12`.

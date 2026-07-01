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
  "today_token_rows": 8,
  "reset_credits_status": "ok",
  "reset_credits_message": "正常",
  "reset_credits_available_count": 2,
  "reset_credits": [
    {
      "status": "available",
      "title": "Full reset (Weekly + 5 hr)",
      "granted_at": "2026-06-18 08:47",
      "expires_at": "2026-07-18 08:47"
    },
    {
      "status": "available",
      "title": "Full reset (Weekly + 5 hr)",
      "granted_at": "2026-06-24 10:37",
      "expires_at": "2026-07-24 10:37"
    }
  ],
  "reset_credits_tooltip": "重置卡: 2 张可用\r\n  1. available | Full reset (Weekly + 5 hr)\r\n     获取 2026-06-18 08:47    过期 2026-07-18 08:47\r\n  2. available | Full reset (Weekly + 5 hr)\r\n     获取 2026-06-24 10:37    过期 2026-07-24 10:37\r\n"
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

`reset_credits*` fields come from `%USERPROFILE%\.codex\auth.json`
`tokens.access_token` plus the ChatGPT reset-credit endpoint. They are omitted
from the tooltip when auth is missing, credentials are unauthorized, or the
request fails. The snapshot only stores the sanitized `available_count` and each
credit's `status`, `title`, `granted_at`, and `expires_at`; token values,
cookies, and credit IDs are never written. `granted_at` and `expires_at` are
display strings converted from UTC into the current computer timezone.

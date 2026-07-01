#include "../../src/CodexUsagePlugin.cpp"

#include <iostream>

int main()
{
    const std::string json =
        "{"
        "\"nested\":{\"five_hour_display\":\"wrong\"},"
        "\"five_hour_display\":\"\\u4e2d\\u6587\","
        "\"weekly_remaining_percent\":67"
        "}";

    if (ExtractJsonString(json, "five_hour_display") != L"中文") {
        std::wcerr << L"five_hour_display did not decode the top-level Unicode string\n";
        return 1;
    }

    auto percent = RemainingPercentFromJson(json, "weekly_remaining_percent", "weekly_used_percent");
    if (!percent || *percent != 67.0) {
        std::cerr << "weekly remaining percent was not parsed\n";
        return 1;
    }

    const std::string escaped = "{\"message\":\"line\\nquote\\\" slash\\\\\"}";
    if (ExtractJsonString(escaped, "message") != L"line\nquote\" slash\\") {
        std::wcerr << L"escaped message did not decode correctly\n";
        return 1;
    }

    return 0;
}

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>

#include "PluginInterface.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <cwctype>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace {

HMODULE g_module{};

constexpr int kDefaultRefreshIntervalSeconds = 60;
constexpr int kMinRefreshIntervalSeconds = 10;
constexpr int kMaxRefreshIntervalSeconds = 3600;
constexpr int kIntervalEditId = 1101;
constexpr int kOpenJsonButtonId = 1102;
constexpr int kOpenLogButtonId = 1103;
constexpr int kOpenDirButtonId = 1104;
constexpr int kRefreshButtonId = 1105;
constexpr int kQuotaBarHeight = 4;
constexpr int kQuotaBarWidth = 36;
constexpr int kQuotaValueGap = 3;

std::wstring Utf8ToWide(const std::string& value)
{
    if (value.empty())
        return {};
    int required = MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (required <= 0)
        return {};
    std::wstring result(required, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), required);
    return result;
}

std::string WideToUtf8(const std::wstring& value)
{
    if (value.empty())
        return {};
    int required = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (required <= 0)
        return {};
    std::string result(required, '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), required, nullptr, nullptr);
    return result;
}

std::wstring GetEnvironmentString(const wchar_t* name)
{
    DWORD required = GetEnvironmentVariableW(name, nullptr, 0);
    if (required == 0)
        return {};
    std::wstring value(required, L'\0');
    DWORD written = GetEnvironmentVariableW(name, value.data(), required);
    if (written == 0)
        return {};
    value.resize(written);
    return value;
}

bool FileExists(const std::wstring& path)
{
    DWORD attrs = GetFileAttributesW(path.c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY) == 0;
}

bool DirectoryExists(const std::wstring& path)
{
    DWORD attrs = GetFileAttributesW(path.c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

std::wstring DirectoryName(const std::wstring& path)
{
    size_t pos = path.find_last_of(L"\\/");
    if (pos == std::wstring::npos)
        return {};
    return path.substr(0, pos);
}

std::wstring JoinPath(const std::wstring& left, const std::wstring& right)
{
    if (left.empty())
        return right;
    wchar_t last = left.back();
    if (last == L'\\' || last == L'/')
        return left + right;
    return left + L"\\" + right;
}

bool EnsureDirectory(const std::wstring& path)
{
    if (path.empty() || DirectoryExists(path))
        return true;

    std::wstring normalized = path;
    for (wchar_t& ch : normalized) {
        if (ch == L'/')
            ch = L'\\';
    }

    size_t start = 0;
    if (normalized.size() >= 3 && normalized[1] == L':' && normalized[2] == L'\\')
        start = 3;

    for (size_t pos = normalized.find(L'\\', start); pos != std::wstring::npos; pos = normalized.find(L'\\', pos + 1)) {
        std::wstring part = normalized.substr(0, pos);
        if (!part.empty() && !DirectoryExists(part))
            CreateDirectoryW(part.c_str(), nullptr);
    }

    return DirectoryExists(normalized) || CreateDirectoryW(normalized.c_str(), nullptr) != FALSE || GetLastError() == ERROR_ALREADY_EXISTS;
}

std::wstring GetModuleDirectory()
{
    std::wstring buffer(MAX_PATH, L'\0');
    DWORD size = GetModuleFileNameW(g_module, buffer.data(), static_cast<DWORD>(buffer.size()));
    while (size == buffer.size()) {
        buffer.resize(buffer.size() * 2);
        size = GetModuleFileNameW(g_module, buffer.data(), static_cast<DWORD>(buffer.size()));
    }
    if (size == 0)
        return {};
    buffer.resize(size);
    return DirectoryName(buffer);
}

std::optional<std::string> ReadFileUtf8(const std::wstring& path)
{
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return std::nullopt;

    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 || size.QuadPart > 4 * 1024 * 1024) {
        CloseHandle(file);
        return std::nullopt;
    }

    std::string data(static_cast<size_t>(size.QuadPart), '\0');
    DWORD read{};
    BOOL ok = data.empty() || ReadFile(file, data.data(), static_cast<DWORD>(data.size()), &read, nullptr);
    CloseHandle(file);
    if (!ok)
        return std::nullopt;
    data.resize(read);
    if (data.size() >= 3 && static_cast<unsigned char>(data[0]) == 0xEF && static_cast<unsigned char>(data[1]) == 0xBB && static_cast<unsigned char>(data[2]) == 0xBF)
        data.erase(0, 3);
    return data;
}

std::wstring QuoteArg(const std::wstring& arg)
{
    std::wstring quoted = L"\"";
    for (wchar_t ch : arg) {
        if (ch == L'"')
            quoted += L"\\\"";
        else
            quoted += ch;
    }
    quoted += L"\"";
    return quoted;
}

std::wstring FormatLastError(DWORD error)
{
    if (error == 0)
        return L"0";

    wchar_t* buffer{};
    DWORD size = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        error,
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<LPWSTR>(&buffer),
        0,
        nullptr);

    std::wstring message = std::to_wstring(error);
    if (size != 0 && buffer != nullptr) {
        message += L" ";
        message += buffer;
        while (!message.empty() && (message.back() == L'\r' || message.back() == L'\n' || message.back() == L' '))
            message.pop_back();
    }
    if (buffer != nullptr)
        LocalFree(buffer);
    return message;
}

std::wstring NowLocalText()
{
    SYSTEMTIME time{};
    GetLocalTime(&time);
    wchar_t buffer[32]{};
    swprintf_s(buffer, L"%04u-%02u-%02u %02u:%02u:%02u", time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute, time.wSecond);
    return buffer;
}

std::string JsonEscapeUtf8(const std::wstring& value)
{
    std::string input = WideToUtf8(value);
    std::string output;
    output.reserve(input.size() + 16);
    for (unsigned char ch : input) {
        switch (ch) {
        case '\\': output += "\\\\"; break;
        case '"': output += "\\\""; break;
        case '\b': output += "\\b"; break;
        case '\f': output += "\\f"; break;
        case '\n': output += "\\n"; break;
        case '\r': output += "\\r"; break;
        case '\t': output += "\\t"; break;
        default:
            if (ch < 0x20) {
                char escaped[8]{};
                sprintf_s(escaped, "\\u%04x", ch);
                output += escaped;
            } else {
                output.push_back(static_cast<char>(ch));
            }
            break;
        }
    }
    return output;
}

bool WriteUtf8File(const std::wstring& path, const std::string& content)
{
    EnsureDirectory(DirectoryName(path));
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return false;
    DWORD written{};
    BOOL ok = content.empty() || WriteFile(file, content.data(), static_cast<DWORD>(content.size()), &written, nullptr);
    CloseHandle(file);
    return ok != FALSE;
}

void AppendUtf8File(const std::wstring& path, const std::string& content)
{
    EnsureDirectory(DirectoryName(path));
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
        return;
    DWORD written{};
    if (!content.empty())
        WriteFile(file, content.data(), static_cast<DWORD>(content.size()), &written, nullptr);
    CloseHandle(file);
}

std::optional<size_t> FindJsonValueStart(const std::string& json, const std::string& key)
{
    std::string needle = "\"" + key + "\"";
    size_t key_pos = json.find(needle);
    if (key_pos == std::string::npos)
        return std::nullopt;
    size_t colon = json.find(':', key_pos + needle.size());
    if (colon == std::string::npos)
        return std::nullopt;
    size_t pos = colon + 1;
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\r' || json[pos] == '\n'))
        ++pos;
    if (pos >= json.size())
        return std::nullopt;
    return pos;
}

std::wstring ExtractJsonString(const std::string& json, const std::string& key, const std::wstring& fallback = L"")
{
    auto start = FindJsonValueStart(json, key);
    if (!start || json[*start] != '"')
        return fallback;

    std::string value;
    bool escaped = false;
    for (size_t pos = *start + 1; pos < json.size(); ++pos) {
        char ch = json[pos];
        if (escaped) {
            switch (ch) {
            case '"': value.push_back('"'); break;
            case '\\': value.push_back('\\'); break;
            case '/': value.push_back('/'); break;
            case 'b': value.push_back('\b'); break;
            case 'f': value.push_back('\f'); break;
            case 'n': value.push_back('\n'); break;
            case 'r': value.push_back('\r'); break;
            case 't': value.push_back('\t'); break;
            default: value.push_back(ch); break;
            }
            escaped = false;
            continue;
        }
        if (ch == '\\') {
            escaped = true;
            continue;
        }
        if (ch == '"')
            return Utf8ToWide(value);
        value.push_back(ch);
    }
    return fallback;
}

std::optional<double> ExtractJsonNumber(const std::string& json, const std::string& key)
{
    auto start = FindJsonValueStart(json, key);
    if (!start)
        return std::nullopt;
    char* end{};
    double value = std::strtod(json.c_str() + *start, &end);
    if (end == json.c_str() + *start)
        return std::nullopt;
    return value;
}

std::wstring FormatTooltipLine(const wchar_t* label, const std::wstring& value)
{
    std::wstring line(label);
    line += L": ";
    line += value.empty() ? L"--" : value;
    line += L"\r\n";
    return line;
}

std::optional<int> ParseRefreshIntervalSeconds(const std::wstring& text)
{
    const wchar_t* begin = text.c_str();
    while (*begin != L'\0' && iswspace(*begin))
        ++begin;

    wchar_t* end{};
    long value = wcstol(begin, &end, 10);
    if (end == begin)
        return std::nullopt;

    while (*end != L'\0' && iswspace(*end))
        ++end;
    if (*end != L'\0')
        return std::nullopt;
    if (value < kMinRefreshIntervalSeconds || value > kMaxRefreshIntervalSeconds)
        return std::nullopt;
    return static_cast<int>(value);
}

void ApplyDefaultFont(HWND hwnd)
{
    SendMessageW(hwnd, WM_SETFONT, reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
}

HWND CreateChildControl(
    HWND parent,
    const wchar_t* class_name,
    const std::wstring& text,
    DWORD style,
    DWORD ex_style,
    int id,
    int x,
    int y,
    int width,
    int height)
{
    HWND control = CreateWindowExW(
        ex_style,
        class_name,
        text.c_str(),
        WS_CHILD | WS_VISIBLE | style,
        x,
        y,
        width,
        height,
        parent,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
        g_module,
        nullptr);
    if (control != nullptr)
        ApplyDefaultFont(control);
    return control;
}

void CenterWindowOnParent(HWND window, HWND parent)
{
    RECT window_rect{};
    GetWindowRect(window, &window_rect);
    int width = window_rect.right - window_rect.left;
    int height = window_rect.bottom - window_rect.top;

    RECT target{};
    if (parent != nullptr && IsWindow(parent))
        GetWindowRect(parent, &target);
    else
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &target, 0);

    int x = target.left + ((target.right - target.left) - width) / 2;
    int y = target.top + ((target.bottom - target.top) - height) / 2;
    SetWindowPos(window, nullptr, x, y, 0, 0, SWP_NOZORDER | SWP_NOSIZE);
}

struct DrawColors {
    COLORREF accent{};
    COLORREF track{};
    COLORREF border{};
    COLORREF text{};
    COLORREF muted{};
};

float ClampRatio(float value)
{
    return std::clamp(value, 0.0f, 1.0f);
}

COLORREF MixColor(COLORREF low, COLORREF high, float ratio)
{
    ratio = ClampRatio(ratio);
    auto mix_channel = [ratio](int a, int b) {
        return static_cast<int>(std::lround(a + ((b - a) * ratio)));
    };
    return RGB(
        mix_channel(GetRValue(low), GetRValue(high)),
        mix_channel(GetGValue(low), GetGValue(high)),
        mix_channel(GetBValue(low), GetBValue(high)));
}

COLORREF RemainingQuotaColor(float remaining_ratio, bool dark_mode)
{
    return dark_mode
        ? MixColor(RGB(232, 92, 82), RGB(56, 196, 122), remaining_ratio)
        : MixColor(RGB(212, 54, 48), RGB(34, 154, 86), remaining_ratio);
}

DrawColors GetQuotaDrawColors(float remaining_ratio, bool dark_mode, bool available)
{
    if (!available) {
        return dark_mode
            ? DrawColors{ RGB(92, 98, 108), RGB(42, 45, 50), RGB(70, 76, 84), RGB(212, 218, 226), RGB(132, 140, 150) }
            : DrawColors{ RGB(150, 156, 164), RGB(232, 236, 240), RGB(188, 194, 202), RGB(48, 54, 60), RGB(128, 136, 146) };
    }

    return dark_mode
        ? DrawColors{ RemainingQuotaColor(remaining_ratio, true), RGB(42, 48, 50), RGB(76, 86, 88), RGB(228, 234, 238), RGB(132, 140, 150) }
        : DrawColors{ RemainingQuotaColor(remaining_ratio, false), RGB(232, 236, 240), RGB(184, 192, 198), RGB(45, 52, 58), RGB(128, 136, 146) };
}

int MeasureTextWidth(HDC dc, const wchar_t* text)
{
    if (dc == nullptr || text == nullptr || *text == L'\0')
        return 0;

    SIZE size{};
    if (!GetTextExtentPoint32W(dc, text, static_cast<int>(wcslen(text)), &size))
        return 0;
    return size.cx;
}

int QuotaLabelSlotWidth(HDC dc)
{
    return std::max(MeasureTextWidth(dc, L"5h"), MeasureTextWidth(dc, L"周"));
}

int QuotaPercentSlotWidth(HDC dc)
{
    return MeasureTextWidth(dc, L"100%");
}

void FillSolidRect(HDC dc, const RECT& rect, COLORREF color)
{
    if (dc == nullptr || rect.right <= rect.left || rect.bottom <= rect.top)
        return;

    HBRUSH brush = CreateSolidBrush(color);
    if (brush == nullptr)
        return;
    FillRect(dc, &rect, brush);
    DeleteObject(brush);
}

void FrameSolidRect(HDC dc, const RECT& rect, COLORREF color)
{
    if (dc == nullptr || rect.right <= rect.left || rect.bottom <= rect.top)
        return;

    HBRUSH brush = CreateSolidBrush(color);
    if (brush == nullptr)
        return;
    FrameRect(dc, &rect, brush);
    DeleteObject(brush);
}

void DrawQuotaBar(
    HDC dc,
    const wchar_t* label_text,
    const wchar_t* value_text,
    const wchar_t* value_sample_text,
    float remaining_ratio,
    int x,
    int y,
    int w,
    int h,
    bool dark_mode)
{
    if (dc == nullptr || label_text == nullptr || value_text == nullptr || value_sample_text == nullptr || w <= 0 || h <= 0)
        return;

    const bool available = value_text[0] != L'-';
    const float ratio = available ? ClampRatio(remaining_ratio) : 0.0f;
    const DrawColors colors = GetQuotaDrawColors(ratio, dark_mode, available);
    const int padding = 4;
    const int gap = 6;
    const int accent_width = 3;
    const int bar_height = kQuotaBarHeight;

    const int label_slot_width = QuotaLabelSlotWidth(dc);
    const int value_width = std::max(MeasureTextWidth(dc, value_text), QuotaPercentSlotWidth(dc));

    RECT rect{ x, y, x + w, y + h };
    RECT accent_rect{ rect.left + padding, rect.top + 2, rect.left + padding + accent_width, rect.bottom - 2 };
    FillSolidRect(dc, accent_rect, colors.accent);

    const int content_left = accent_rect.right + gap;
    const int bar_left = content_left + label_slot_width + gap;
    const int bar_right = bar_left + kQuotaBarWidth;

    const int center_y = rect.top + (h / 2);
    const int bar_top = center_y - (bar_height / 2);
    RECT bar_rect{ bar_left, bar_top, bar_right, bar_top + bar_height };
    RECT fill_rect = bar_rect;
    fill_rect.right = fill_rect.left + static_cast<int>(std::lround((fill_rect.right - fill_rect.left) * ratio));

    FillSolidRect(dc, bar_rect, colors.track);
    FillSolidRect(dc, fill_rect, colors.accent);
    FrameSolidRect(dc, bar_rect, colors.border);

    int old_bk_mode = SetBkMode(dc, TRANSPARENT);
    COLORREF old_text_color = GetTextColor(dc);

    RECT label_rect{ content_left, rect.top, std::max(content_left, bar_left - gap), rect.bottom };
    RECT value_rect{ bar_right + kQuotaValueGap, rect.top, bar_right + kQuotaValueGap + value_width, rect.bottom };

    SetTextColor(dc, colors.text);
    DrawTextW(dc, label_text, -1, &label_rect, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS);

    SetTextColor(dc, available ? colors.text : colors.muted);
    DrawTextW(dc, value_text, -1, &value_rect, DT_RIGHT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS);

    SetTextColor(dc, old_text_color);
    SetBkMode(dc, old_bk_mode);
}

std::optional<double> RemainingPercentFromJson(const std::string& json, const std::string& remaining_key, const std::string& used_key)
{
    if (auto value = ExtractJsonNumber(json, remaining_key))
        return std::clamp(*value, 0.0, 100.0);
    if (auto used = ExtractJsonNumber(json, used_key))
        return std::clamp(100.0 - *used, 0.0, 100.0);
    return std::nullopt;
}

enum class ItemKind {
    FiveHour,
    Weekly,
};

struct UsageSnapshot {
    std::wstring five_hour_display = L"--";
    std::wstring weekly_display = L"--";
    std::wstring reset_display = L"--";
    std::wstring status = L"waiting";
    std::wstring message = L"等待采集 Codex 用量";
    std::wstring generated_at_local = L"--";
    std::wstring plan_type = L"--";
    std::wstring rate_source = L"--";
    float five_hour_graph = 0.0f;
    float weekly_graph = 0.0f;
    std::wstring tooltip = L"Codex 用量：等待采集";
};

std::wstring BuildTooltip(const UsageSnapshot& snapshot)
{
    return
        FormatTooltipLine(L"5 小时剩余额度", snapshot.five_hour_display) +
        FormatTooltipLine(L"周剩余额度", snapshot.weekly_display) +
        FormatTooltipLine(L"重置", snapshot.reset_display);
}

class CodexUsagePlugin;

class CodexUsageItem final : public IPluginItem {
public:
    CodexUsageItem(CodexUsagePlugin& plugin, ItemKind kind, const wchar_t* name, const wchar_t* id, const wchar_t* label, const wchar_t* sample)
        : plugin_(plugin), kind_(kind), name_(name), id_(id), label_(label), sample_(sample)
    {
    }

    const wchar_t* GetItemName() const override { return name_.c_str(); }
    const wchar_t* GetItemId() const override { return id_.c_str(); }
    const wchar_t* GetItemLableText() const override { return label_.c_str(); }
    const wchar_t* GetItemValueText() const override;
    const wchar_t* GetItemValueSampleText() const override { return sample_.c_str(); }
    bool IsCustomDraw() const override;
    int GetItemWidth() const override;
    int GetItemWidthEx(void* hDC) const override;
    void DrawItem(void* hDC, int x, int y, int w, int h, bool dark_mode) override;
    int IsDrawResourceUsageGraph() const override;
    float GetResourceUsageGraphValue() const override;
    int OnMouseEvent(MouseEventType type, int, int, void*, int) override;

private:
    CodexUsagePlugin& plugin_;
    ItemKind kind_;
    std::wstring name_;
    std::wstring id_;
    std::wstring label_;
    std::wstring sample_;
};

class CodexUsagePlugin final : public ITMPlugin {
public:
    static CodexUsagePlugin& Instance()
    {
        static CodexUsagePlugin instance;
        return instance;
    }

    IPluginItem* GetItem(int index) override
    {
        if (index < 0 || index >= static_cast<int>(items_.size()))
            return nullptr;
        return &items_[static_cast<size_t>(index)];
    }

    void DataRequired() override
    {
        LaunchCollector(false, false);
        LoadSnapshot();
    }

    const wchar_t* GetInfo(PluginInfoIndex index) override
    {
        switch (index) {
        case TMI_NAME: return L"Codex Usage";
        case TMI_DESCRIPTION: return L"显示 Codex 5 小时和周剩余额度，提示框显示重置时间";
        case TMI_AUTHOR: return L"Codex";
        case TMI_COPYRIGHT: return L"MIT; PluginInterface.h Copyright (C) by Zhong Yang";
        case TMI_VERSION: return L"0.1.2";
        case TMI_URL: return L"https://github.com/zhongyang219/TrafficMonitor/wiki/%E6%8F%92%E4%BB%B6%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97";
        default: return L"";
        }
    }

    const wchar_t* GetTooltipInfo() override
    {
        return snapshot_.tooltip.c_str();
    }

    void OnExtenedInfo(ExtendedInfoIndex index, const wchar_t* data) override
    {
        if (index == EI_CONFIG_DIR && data != nullptr && data[0] != L'\0') {
            config_dir_ = data;
            status_path_.clear();
            settings_loaded_ = false;
            LoadSettings();
        }
    }

    void OnInitialize(ITrafficMonitor* pApp) override
    {
        app_ = pApp;
        LoadSettings();
        LaunchCollector(false, false);
        LoadSnapshot();
    }

    OptionReturn ShowOptionsDialog(void* hParent) override
    {
        return RunOptionsDialog(static_cast<HWND>(hParent));
    }

    int GetCommandCount() override { return 4; }

    const wchar_t* GetCommandName(int command_index) override
    {
        switch (command_index) {
        case 0: return L"立即刷新 Codex 用量";
        case 1: return L"打开 Codex 用量 JSON";
        case 2: return L"打开 Codex Usage 配置目录";
        case 3: return L"打开 Codex Usage 诊断日志";
        default: return nullptr;
        }
    }

    void OnPluginCommand(int command_index, void* hWnd, void*) override
    {
        HWND parent = static_cast<HWND>(hWnd);
        if (command_index == 0) {
            LaunchCollector(true, true);
            LoadSnapshot();
            Notify(L"Codex Usage 已刷新: " + snapshot_.five_hour_display + L" / " + snapshot_.weekly_display);
        } else if (command_index == 1) {
            OpenTextFile(StatusPath());
        } else if (command_index == 2) {
            EnsureDirectory(DirectoryName(StatusPath()));
            ShellExecuteW(parent, L"open", DirectoryName(StatusPath()).c_str(), nullptr, nullptr, SW_SHOWNORMAL);
        } else if (command_index == 3) {
            OpenTextFile(LogPath());
        }
    }

    const wchar_t* ItemText(ItemKind kind) const
    {
        switch (kind) {
        case ItemKind::FiveHour: return snapshot_.five_hour_display.c_str();
        case ItemKind::Weekly: return snapshot_.weekly_display.c_str();
        default: return L"--";
        }
    }

    float GraphValue(ItemKind kind) const
    {
        switch (kind) {
        case ItemKind::FiveHour: return snapshot_.five_hour_graph;
        case ItemKind::Weekly: return snapshot_.weekly_graph;
        default: return 0.0f;
        }
    }

    void RefreshNow()
    {
        LaunchCollector(true, true);
        LoadSnapshot();
    }

private:
    CodexUsagePlugin()
        : items_{
            CodexUsageItem(*this, ItemKind::FiveHour, L"Codex 5 小时额度", L"CodexFiveHourQuota", L"5h", L"100%"),
            CodexUsageItem(*this, ItemKind::Weekly, L"Codex 周额度", L"CodexWeeklyQuota", L"周", L"100%")
        }
    {
    }

    struct OptionsDialogState {
        CodexUsagePlugin* plugin{};
        HWND interval_edit{};
        bool saved{};
    };

    std::wstring ConfigPath()
    {
        return JoinPath(DirectoryName(StatusPath()), L"codex_usage_plugin.ini");
    }

    void LoadSettings()
    {
        if (settings_loaded_)
            return;

        std::wstring path = ConfigPath();
        int value = static_cast<int>(GetPrivateProfileIntW(
            L"settings",
            L"refresh_interval_seconds",
            kDefaultRefreshIntervalSeconds,
            path.c_str()));
        refresh_interval_seconds_ = std::clamp(value, kMinRefreshIntervalSeconds, kMaxRefreshIntervalSeconds);
        settings_loaded_ = true;

        if (!FileExists(path))
            SaveSettings();
    }

    void SaveSettings()
    {
        std::wstring path = ConfigPath();
        EnsureDirectory(DirectoryName(path));
        std::wstring value = std::to_wstring(refresh_interval_seconds_);
        WritePrivateProfileStringW(L"settings", L"refresh_interval_seconds", value.c_str(), path.c_str());
    }

    ULONGLONG RefreshIntervalMilliseconds()
    {
        LoadSettings();
        return static_cast<ULONGLONG>(refresh_interval_seconds_) * 1000ULL;
    }

    static LRESULT CALLBACK OptionsWndProc(HWND hwnd, UINT message, WPARAM w_param, LPARAM l_param)
    {
        auto* state = reinterpret_cast<OptionsDialogState*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
        if (message == WM_NCCREATE) {
            auto* create = reinterpret_cast<CREATESTRUCTW*>(l_param);
            state = reinterpret_cast<OptionsDialogState*>(create->lpCreateParams);
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(state));
        }

        switch (message) {
        case WM_CREATE:
            if (state != nullptr)
                state->plugin->CreateOptionsControls(hwnd, *state);
            return 0;
        case WM_COMMAND:
            if (state != nullptr && state->plugin->HandleOptionsCommand(hwnd, *state, LOWORD(w_param)))
                return 0;
            break;
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            return 0;
        default:
            break;
        }
        return DefWindowProcW(hwnd, message, w_param, l_param);
    }

    void CreateOptionsControls(HWND hwnd, OptionsDialogState& state)
    {
        const int margin = 14;
        int y = 16;
        const int label_width = 150;
        const int full_width = 560;

        CreateChildControl(hwnd, L"STATIC", L"刷新时间间隔（秒，10-3600）:", 0, 0, -1, margin, y + 4, label_width + 40, 22);
        state.interval_edit = CreateChildControl(
            hwnd,
            L"EDIT",
            std::to_wstring(refresh_interval_seconds_),
            WS_TABSTOP | ES_AUTOHSCROLL | WS_BORDER,
            WS_EX_CLIENTEDGE,
            kIntervalEditId,
            margin + label_width + 50,
            y,
            90,
            24);

        y += 40;
        CreateChildControl(hwnd, L"STATIC", L"状态 JSON:", 0, 0, -1, margin, y, label_width, 20);
        CreateChildControl(hwnd, L"EDIT", StatusPath(), ES_READONLY | ES_AUTOHSCROLL | WS_BORDER, WS_EX_CLIENTEDGE, -1, margin, y + 22, full_width, 24);

        y += 58;
        CreateChildControl(hwnd, L"STATIC", L"诊断日志:", 0, 0, -1, margin, y, label_width, 20);
        CreateChildControl(hwnd, L"EDIT", LogPath(), ES_READONLY | ES_AUTOHSCROLL | WS_BORDER, WS_EX_CLIENTEDGE, -1, margin, y + 22, full_width, 24);

        y += 58;
        CreateChildControl(hwnd, L"STATIC", L"采集脚本:", 0, 0, -1, margin, y, label_width, 20);
        CreateChildControl(hwnd, L"EDIT", CollectorScriptPath(), ES_READONLY | ES_AUTOHSCROLL | WS_BORDER, WS_EX_CLIENTEDGE, -1, margin, y + 22, full_width, 24);

        y += 62;
        std::wstring status = L"当前状态: " + snapshot_.status;
        CreateChildControl(hwnd, L"STATIC", status, 0, 0, -1, margin, y, full_width, 22);

        y += 42;
        CreateChildControl(hwnd, L"BUTTON", L"保存并刷新", WS_TABSTOP | BS_DEFPUSHBUTTON, 0, IDOK, margin, y, 104, 30);
        CreateChildControl(hwnd, L"BUTTON", L"立即刷新", WS_TABSTOP | BS_PUSHBUTTON, 0, kRefreshButtonId, margin + 112, y, 88, 30);
        CreateChildControl(hwnd, L"BUTTON", L"打开 JSON", WS_TABSTOP | BS_PUSHBUTTON, 0, kOpenJsonButtonId, margin + 208, y, 88, 30);
        CreateChildControl(hwnd, L"BUTTON", L"打开日志", WS_TABSTOP | BS_PUSHBUTTON, 0, kOpenLogButtonId, margin + 304, y, 88, 30);
        CreateChildControl(hwnd, L"BUTTON", L"打开目录", WS_TABSTOP | BS_PUSHBUTTON, 0, kOpenDirButtonId, margin + 400, y, 88, 30);
        CreateChildControl(hwnd, L"BUTTON", L"取消", WS_TABSTOP | BS_PUSHBUTTON, 0, IDCANCEL, margin + 496, y, 78, 30);
    }

    bool HandleOptionsCommand(HWND hwnd, OptionsDialogState& state, int command_id)
    {
        switch (command_id) {
        case IDOK: {
            wchar_t buffer[32]{};
            GetWindowTextW(state.interval_edit, buffer, static_cast<int>(sizeof(buffer) / sizeof(buffer[0])));
            auto parsed = ParseRefreshIntervalSeconds(buffer);
            if (!parsed) {
                std::wstring message = L"请输入 " + std::to_wstring(kMinRefreshIntervalSeconds) + L"-" + std::to_wstring(kMaxRefreshIntervalSeconds) + L" 之间的刷新间隔秒数。";
                MessageBoxW(hwnd, message.c_str(), L"Codex Usage", MB_OK | MB_ICONWARNING);
                SetFocus(state.interval_edit);
                return true;
            }

            refresh_interval_seconds_ = *parsed;
            SaveSettings();
            last_collector_launch_tick_ = 0;
            LaunchCollector(true, true);
            LoadSnapshot();
            state.saved = true;
            DestroyWindow(hwnd);
            return true;
        }
        case IDCANCEL:
            DestroyWindow(hwnd);
            return true;
        case kRefreshButtonId:
            LaunchCollector(true, true);
            LoadSnapshot();
            MessageBoxW(hwnd, L"Codex 用量已刷新。", L"Codex Usage", MB_OK | MB_ICONINFORMATION);
            return true;
        case kOpenJsonButtonId:
            OpenTextFile(StatusPath());
            return true;
        case kOpenLogButtonId:
            OpenTextFile(LogPath());
            return true;
        case kOpenDirButtonId:
            EnsureDirectory(DirectoryName(StatusPath()));
            ShellExecuteW(hwnd, L"open", DirectoryName(StatusPath()).c_str(), nullptr, nullptr, SW_SHOWNORMAL);
            return true;
        default:
            return false;
        }
    }

    OptionReturn RunOptionsDialog(HWND parent)
    {
        LoadSettings();
        LoadSnapshot();

        const wchar_t* class_name = L"CodexUsageOptionsWindow";
        static bool registered = false;
        if (!registered) {
            WNDCLASSEXW window_class{};
            window_class.cbSize = sizeof(window_class);
            window_class.lpfnWndProc = OptionsWndProc;
            window_class.hInstance = g_module;
            window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
            window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
            window_class.lpszClassName = class_name;
            registered = RegisterClassExW(&window_class) != 0 || GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
        }

        OptionsDialogState state{};
        state.plugin = this;

        HWND hwnd = CreateWindowExW(
            WS_EX_DLGMODALFRAME,
            class_name,
            L"Codex Usage 选项",
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            610,
            360,
            parent,
            nullptr,
            g_module,
            &state);
        if (hwnd == nullptr)
            return OR_OPTION_UNCHANGED;

        CenterWindowOnParent(hwnd, parent);
        if (parent != nullptr && IsWindow(parent))
            EnableWindow(parent, FALSE);
        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);

        MSG msg{};
        while (IsWindow(hwnd) && GetMessageW(&msg, nullptr, 0, 0) > 0) {
            if (!IsDialogMessageW(hwnd, &msg)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }

        if (parent != nullptr && IsWindow(parent)) {
            EnableWindow(parent, TRUE);
            SetActiveWindow(parent);
        }
        return state.saved ? OR_OPTION_CHANGED : OR_OPTION_UNCHANGED;
    }

    std::wstring StatusPath()
    {
        std::wstring override_path = GetEnvironmentString(L"CODEX_TRAFFICMONITOR_USAGE_JSON");
        if (!override_path.empty())
            return override_path;

        if (!status_path_.empty())
            return status_path_;

        if (!config_dir_.empty()) {
            status_path_ = JoinPath(JoinPath(config_dir_, L"CodexUsage"), L"codex_usage_status.json");
            return status_path_;
        }

        std::wstring user_profile = GetEnvironmentString(L"USERPROFILE");
        if (!user_profile.empty()) {
            status_path_ = JoinPath(JoinPath(JoinPath(user_profile, L".codex"), L"trafficmonitor"), L"codex_usage_status.json");
            return status_path_;
        }

        status_path_ = JoinPath(GetModuleDirectory(), L"codex_usage_status.json");
        return status_path_;
    }

    std::wstring LogPath()
    {
        return JoinPath(DirectoryName(StatusPath()), L"codex_usage_plugin.log");
    }

    std::wstring CollectorScriptPath() const
    {
        std::wstring module_dir = GetModuleDirectory();
        std::vector<std::wstring> candidates{
            JoinPath(JoinPath(module_dir, L"scripts"), L"update_codex_usage.ps1"),
            JoinPath(JoinPath(JoinPath(module_dir, L"CodexUsage"), L"scripts"), L"update_codex_usage.ps1"),
            JoinPath(module_dir, L"update_codex_usage.ps1")
        };
        for (const auto& candidate : candidates) {
            if (FileExists(candidate))
                return candidate;
        }
        return candidates.front();
    }

    void Notify(const std::wstring& message)
    {
        if (app_ != nullptr)
            app_->ShowNotifyMessage(message.c_str());
    }

    void WriteLog(const std::wstring& message)
    {
        std::wstring line = NowLocalText() + L" " + message + L"\r\n";
        AppendUtf8File(LogPath(), WideToUtf8(line));
    }

    void WriteStatusSnapshot(const std::wstring& status, const std::wstring& message, const std::wstring& reset_display)
    {
        std::ostringstream json;
        json << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"status\": \"" << JsonEscapeUtf8(status) << "\",\n"
             << "  \"message\": \"" << JsonEscapeUtf8(message) << "\",\n"
             << "  \"generated_at_local\": \"" << JsonEscapeUtf8(NowLocalText()) << "\",\n"
             << "  \"five_hour_display\": \"--\",\n"
             << "  \"five_hour_remaining_percent\": null,\n"
             << "  \"weekly_display\": \"--\",\n"
             << "  \"weekly_remaining_percent\": null,\n"
             << "  \"reset_display\": \"" << JsonEscapeUtf8(reset_display) << "\",\n"
             << "  \"today_tokens_display\": \"--\",\n"
             << "  \"rate_limits_source\": \"plugin\",\n"
             << "  \"today_token_source\": \"plugin\"\n"
             << "}\n";
        WriteUtf8File(StatusPath(), json.str());
    }

    std::wstring PowerShellPath() const
    {
        std::wstring system_dir(MAX_PATH, L'\0');
        UINT written = GetSystemDirectoryW(system_dir.data(), static_cast<UINT>(system_dir.size()));
        if (written > 0 && written < system_dir.size()) {
            system_dir.resize(written);
            std::wstring candidate = JoinPath(JoinPath(JoinPath(system_dir, L"WindowsPowerShell"), L"v1.0"), L"powershell.exe");
            if (FileExists(candidate))
                return candidate;
        }
        return L"powershell.exe";
    }

    void OpenTextFile(const std::wstring& path)
    {
        EnsureDirectory(DirectoryName(path));
        if (!FileExists(path)) {
            WriteUtf8File(path, "{}\r\n");
        }
        std::wstring params = QuoteArg(path);
        ShellExecuteW(nullptr, L"open", L"notepad.exe", params.c_str(), nullptr, SW_SHOWNORMAL);
    }

    bool LaunchCollector(bool force, bool wait)
    {
        LoadSettings();
        const ULONGLONG now = GetTickCount64();
        if (!force && last_collector_launch_tick_ != 0 && now - last_collector_launch_tick_ < RefreshIntervalMilliseconds())
            return false;

        std::wstring script = CollectorScriptPath();
        if (!FileExists(script)) {
            std::wstring message = L"未找到采集脚本: " + script;
            WriteLog(message);
            WriteStatusSnapshot(L"error", message, L"脚本缺失");
            return false;
        }

        std::wstring output_path = StatusPath();
        std::wstring log_path = LogPath();
        EnsureDirectory(DirectoryName(output_path));

        if (!FileExists(output_path))
            WriteStatusSnapshot(L"collecting", L"正在生成 Codex 用量快照", L"采集中");

        std::wstring powershell = PowerShellPath();
        std::wstring command = QuoteArg(powershell);
        command += L" -NoProfile -ExecutionPolicy Bypass -File ";
        command += QuoteArg(script);
        command += L" -OutputPath ";
        command += QuoteArg(output_path);
        command += L" -LogPath ";
        command += QuoteArg(log_path);

        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        startup.dwFlags = STARTF_USESHOWWINDOW;
        startup.wShowWindow = SW_HIDE;
        PROCESS_INFORMATION process{};

        WriteLog(L"启动采集: " + command);
        std::vector<wchar_t> mutable_command(command.begin(), command.end());
        mutable_command.push_back(L'\0');
        if (CreateProcessW(nullptr, mutable_command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr, GetModuleDirectory().c_str(), &startup, &process)) {
            DWORD exit_code = STILL_ACTIVE;
            if (wait) {
                DWORD wait_result = WaitForSingleObject(process.hProcess, 15000);
                if (wait_result == WAIT_OBJECT_0 && GetExitCodeProcess(process.hProcess, &exit_code)) {
                    WriteLog(L"采集进程退出: " + std::to_wstring(exit_code));
                } else if (wait_result == WAIT_TIMEOUT) {
                    WriteLog(L"采集进程仍在运行，已超过 15 秒等待窗口");
                    Notify(L"Codex Usage 采集仍在运行，请稍后查看");
                } else {
                    DWORD error = GetLastError();
                    WriteLog(L"等待采集进程失败: " + FormatLastError(error));
                }
            }
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
            last_collector_launch_tick_ = now;
            return true;
        }

        DWORD error = GetLastError();
        std::wstring message = L"启动 PowerShell 采集失败: " + FormatLastError(error);
        WriteLog(message);
        WriteStatusSnapshot(L"error", message, L"启动失败");
        Notify(message);
        return false;
    }

    void LoadSnapshot()
    {
        LoadSettings();
        std::wstring path = StatusPath();
        auto content = ReadFileUtf8(path);
        if (!content) {
            UsageSnapshot waiting;
            waiting.message = FileExists(CollectorScriptPath()) ? L"正在生成 Codex 用量快照" : L"未找到采集脚本或状态 JSON";
            waiting.reset_display = L"采集中";
            waiting.tooltip = BuildTooltip(waiting);
            snapshot_ = waiting;
            return;
        }

        const std::string& json = *content;
        UsageSnapshot next;
        next.five_hour_display = ExtractJsonString(json, "five_hour_display", L"--");
        next.weekly_display = ExtractJsonString(json, "weekly_display", L"--");
        next.reset_display = ExtractJsonString(json, "reset_display", L"--");
        next.status = ExtractJsonString(json, "status", L"unknown");
        next.message = ExtractJsonString(json, "message", L"");
        next.generated_at_local = ExtractJsonString(json, "generated_at_local", L"--");
        next.plan_type = ExtractJsonString(json, "plan_type", L"--");
        next.rate_source = ExtractJsonString(json, "rate_limits_source", L"--");

        if (auto value = RemainingPercentFromJson(json, "five_hour_remaining_percent", "five_hour_used_percent"))
            next.five_hour_graph = static_cast<float>(std::max(0.0, std::min(1.0, *value / 100.0)));
        if (auto value = RemainingPercentFromJson(json, "weekly_remaining_percent", "weekly_used_percent"))
            next.weekly_graph = static_cast<float>(std::max(0.0, std::min(1.0, *value / 100.0)));

        next.tooltip = BuildTooltip(next);

        snapshot_ = next;
    }

    std::array<CodexUsageItem, 2> items_;
    UsageSnapshot snapshot_;
    std::wstring config_dir_;
    std::wstring status_path_;
    ITrafficMonitor* app_{};
    bool settings_loaded_{};
    int refresh_interval_seconds_{kDefaultRefreshIntervalSeconds};
    ULONGLONG last_collector_launch_tick_{};
};

const wchar_t* CodexUsageItem::GetItemValueText() const
{
    return plugin_.ItemText(kind_);
}

bool CodexUsageItem::IsCustomDraw() const
{
    return kind_ == ItemKind::FiveHour || kind_ == ItemKind::Weekly;
}

int CodexUsageItem::GetItemWidth() const
{
    return IsCustomDraw() ? 96 : 0;
}

int CodexUsageItem::GetItemWidthEx(void* hDC) const
{
    if (!IsCustomDraw())
        return 0;

    HDC dc = static_cast<HDC>(hDC);
    if (dc == nullptr)
        return GetItemWidth();

    const int padding = 4;
    const int gap = 6;
    const int accent_width = 3;
    const int label_slot_width = QuotaLabelSlotWidth(dc);
    const int value_width = QuotaPercentSlotWidth(dc);
    return padding * 2 + accent_width + gap + label_slot_width + gap + kQuotaBarWidth + kQuotaValueGap + value_width;
}

void CodexUsageItem::DrawItem(void* hDC, int x, int y, int w, int h, bool dark_mode)
{
    if (!IsCustomDraw())
        return;

    DrawQuotaBar(
        static_cast<HDC>(hDC),
        GetItemLableText(),
        plugin_.ItemText(kind_),
        GetItemValueSampleText(),
        plugin_.GraphValue(kind_),
        x,
        y,
        w,
        h,
        dark_mode);
}

int CodexUsageItem::IsDrawResourceUsageGraph() const
{
    return 0;
}

float CodexUsageItem::GetResourceUsageGraphValue() const
{
    return plugin_.GraphValue(kind_);
}

int CodexUsageItem::OnMouseEvent(MouseEventType type, int, int, void*, int)
{
    if (type == MT_DBCLICKED) {
        plugin_.RefreshNow();
        return 1;
    }
    return 0;
}

} // namespace

extern "C" __declspec(dllexport) ITMPlugin* TMPluginGetInstance()
{
    return &CodexUsagePlugin::Instance();
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

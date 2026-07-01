cmake_minimum_required(VERSION 3.20)
project(TrafficMonitorCodexUsage LANGUAGES CXX)

enable_testing()

add_library(CodexUsage SHARED
    src/CodexUsagePlugin.cpp
)

target_include_directories(CodexUsage PRIVATE include)
target_compile_features(CodexUsage PRIVATE cxx_std_17)
target_compile_definitions(CodexUsage PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
    target_compile_options(CodexUsage PRIVATE /utf-8)
endif()
target_link_libraries(CodexUsage PRIVATE Shell32 User32 Gdi32)
set_target_properties(CodexUsage PROPERTIES OUTPUT_NAME "CodexUsage")

add_custom_command(TARGET CodexUsage POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/scripts
            $<TARGET_FILE_DIR:CodexUsage>/scripts
)

add_executable(CodexUsageJsonParserSmoke
    tests/cpp/json_parser_smoke.cpp
)
target_include_directories(CodexUsageJsonParserSmoke PRIVATE include)
target_compile_features(CodexUsageJsonParserSmoke PRIVATE cxx_std_17)
target_compile_definitions(CodexUsageJsonParserSmoke PRIVATE CODEX_USAGE_PLUGIN_TEST UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
    target_compile_options(CodexUsageJsonParserSmoke PRIVATE /utf-8)
endif()
target_link_libraries(CodexUsageJsonParserSmoke PRIVATE Shell32 User32 Gdi32)
add_test(NAME CodexUsageJsonParserSmoke COMMAND CodexUsageJsonParserSmoke)

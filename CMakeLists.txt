cmake_minimum_required(VERSION 3.20)
project(TrafficMonitorCodexUsage LANGUAGES CXX)

add_library(CodexUsage SHARED
    src/CodexUsagePlugin.cpp
)

target_include_directories(CodexUsage PRIVATE include)
target_compile_features(CodexUsage PRIVATE cxx_std_17)
target_compile_definitions(CodexUsage PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(CodexUsage PRIVATE Shell32 User32 Gdi32)
set_target_properties(CodexUsage PROPERTIES OUTPUT_NAME "CodexUsage")

add_custom_command(TARGET CodexUsage POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/scripts
            $<TARGET_FILE_DIR:CodexUsage>/scripts
)

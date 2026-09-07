#pragma once

#include <string>

#if __cplusplus < 202002L
#error "C++20 compile flags did not reach the compiler"
#endif

std::string greet(std::string_view who);

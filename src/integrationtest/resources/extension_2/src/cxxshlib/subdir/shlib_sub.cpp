#include "cxxcommon.hpp"

#define EXPORT __attribute__((visibility("default")))

extern "C" EXPORT unsigned long shlib_greeting_length(const char *s) {
    return std::string(s).size();
}

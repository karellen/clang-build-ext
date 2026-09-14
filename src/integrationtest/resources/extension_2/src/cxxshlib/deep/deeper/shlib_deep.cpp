#include "cxxcommon.hpp"

#define EXPORT __attribute__((visibility("default")))

extern "C" EXPORT unsigned long shlib_deep_length(const char *s) {
    return std::string(s).size() + 1;
}

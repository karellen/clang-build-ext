#include "cxxcommon.hpp"

#define EXPORT __attribute__((visibility("default")))

extern "C" EXPORT const char *shlib_greeting() {
    static const std::string greeting = std::string("hello, ") + "shlib";
    return greeting.c_str();
}

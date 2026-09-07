#include "cxxcommon.hpp"

#ifndef CXXALIB_FLAVOR
#error "CXXALIB_FLAVOR macro was not passed to the compiler"
#endif

std::string greet_loudly(std::string_view who) {
    return greet(who).append("!");
}

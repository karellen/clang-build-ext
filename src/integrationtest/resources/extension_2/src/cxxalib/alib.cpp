#include "cxxcommon.hpp"

#ifndef CXXALIB_FLAVOR
#error "CXXALIB_FLAVOR macro was not passed to the compiler"
#endif

std::string greet(std::string_view who) {
    return std::string("hello, ").append(who);
}

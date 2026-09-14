#include <Python.h>

#include "cxxcommon.hpp"

std::string module_greeting();

// Defined three directories down, so a non-recursive ** leaves this undefined
// and the extension fails to load rather than silently losing a source file.
std::string module_greeting_deep();

static PyObject *method_test(PyObject *self, PyObject *args) {
    const std::string greeting = module_greeting() + module_greeting_deep();
    return PyUnicode_FromStringAndSize(greeting.data(), greeting.size());
}

static PyMethodDef TestMethods[] = {
    {"test", method_test, METH_VARARGS, "Python test function"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef testModule = {
    PyModuleDef_HEAD_INIT,
    "test_cxx",
    "Python C++ test module",
    -1,
    TestMethods
};

PyMODINIT_FUNC PyInit_test_cxx(void) {
    return PyModule_Create(&testModule);
}

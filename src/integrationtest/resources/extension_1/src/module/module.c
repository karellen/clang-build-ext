#include <Python.h>

static PyObject *method_test(PyObject *self, PyObject *args) {
    return PyLong_FromLong(0l);
}

static PyMethodDef TestMethods[] = {
    {"test", method_test, METH_VARARGS, "Python test function"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef testModule = {
    PyModuleDef_HEAD_INIT,
    "test",
    "Python test module",
    -1,
    TestMethods
};

PyMODINIT_FUNC PyInit_test(void) {
    return PyModule_Create(&testModule);
}

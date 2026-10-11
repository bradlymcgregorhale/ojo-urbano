#!/usr/bin/env python3
"""Ejecuta la suite sin red ni pesos; conserva python pruebas.py."""
import runpy

if __name__ == "__main__":
    runpy.run_module("tests.pruebas", run_name="__main__")

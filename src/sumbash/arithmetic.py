#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#pylint:disable=W0301
#
# Copyright 2018-2026 William Martinez Bas <metfar@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
"""Safe arithmetic evaluator used by sumbash.

Unlike historical Bash arithmetic, SUM arithmetic keeps fractional values.
""";

import ast;
from decimal import Decimal, ROUND_HALF_UP;
import math;
import operator;


class SumArithmeticError(ValueError):
    pass;


def _number(value):
    if isinstance(value, bool): return int(value);
    if isinstance(value, (int, float, Decimal)): return value;
    text = str(value).strip();
    if not text: return 0;
    try:
        if any(c in text.lower() for c in (".", "e")): return float(text);
        return int(text, 0);
    except ValueError as exc:
        raise SumArithmeticError("not a number: {}".format(value)) from exc;


def _int_exact(value):
    value = _number(value);
    if isinstance(value, float) and not value.is_integer():
        raise SumArithmeticError("bitwise operations require integer operands");
    if isinstance(value, Decimal) and value != value.to_integral_value():
        raise SumArithmeticError("bitwise operations require integer operands");
    return int(value);


def _round(value, digits=0):
    value = _number(value);
    digits = int(_number(digits));
    quantum = Decimal("1").scaleb(-digits);
    result = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP);
    return int(result) if digits <= 0 else float(result);


def _int(value): return int(_number(value));
def _floor(value): return math.floor(_number(value));
def _ceil(value): return math.ceil(_number(value));
def _sqrt(value):
    value = _number(value);
    if value < 0: raise SumArithmeticError("sqrt domain error");
    return math.sqrt(value);

def _pow(a, b): return math.pow(_number(a), _number(b));

def _abs(value): return abs(_number(value));


_FUNCTIONS = {
    "abs": _abs,
    "int": _int,
    "round": _round,
    "floor": _floor,
    "ceil": _ceil,
    "sqrt": _sqrt,
    "pow": _pow,
};


def format_number(value):
    if isinstance(value, bool): return "1" if value else "0";
    if isinstance(value, int): return str(value);
    if isinstance(value, Decimal):
        if value == value.to_integral_value(): return str(int(value));
        return format(value.normalize(), "f").rstrip("0").rstrip(".");
    if isinstance(value, float):
        if not math.isfinite(value): return str(value);
        if value.is_integer(): return str(int(value));
        return format(value, ".15g");
    return str(value);


class _Evaluator(ast.NodeVisitor):
    def __init__(self, variables=None):
        self.variables = dict(variables or {});

    def visit_Expression(self, node): return self.visit(node.body);
    def visit_Constant(self, node):
        if isinstance(node.value, (int, float, bool)): return node.value;
        raise SumArithmeticError("unsupported constant");

    def visit_Name(self, node):
        if node.id in self.variables: return _number(self.variables[node.id]);
        if node.id in ("true", "TRUE"): return 1;
        if node.id in ("false", "FALSE"): return 0;
        return 0;

    def visit_UnaryOp(self, node):
        value = self.visit(node.operand);
        if isinstance(node.op, ast.UAdd): return +_number(value);
        if isinstance(node.op, ast.USub): return -_number(value);
        if isinstance(node.op, ast.Not): return 0 if bool(value) else 1;
        if isinstance(node.op, ast.Invert): return ~_int_exact(value);
        raise SumArithmeticError("unsupported unary operator");

    def visit_BinOp(self, node):
        left = self.visit(node.left); right = self.visit(node.right);
        if isinstance(node.op, ast.Add): return _number(left) + _number(right);
        if isinstance(node.op, ast.Sub): return _number(left) - _number(right);
        if isinstance(node.op, ast.Mult): return _number(left) * _number(right);
        if isinstance(node.op, ast.Div): return _number(left) / _number(right);
        if isinstance(node.op, ast.FloorDiv): return _number(left) // _number(right);
        if isinstance(node.op, ast.Mod): return _number(left) % _number(right);
        if isinstance(node.op, ast.Pow): return _number(left) ** _number(right);
        if isinstance(node.op, ast.LShift): return _int_exact(left) << _int_exact(right);
        if isinstance(node.op, ast.RShift): return _int_exact(left) >> _int_exact(right);
        if isinstance(node.op, ast.BitAnd): return _int_exact(left) & _int_exact(right);
        if isinstance(node.op, ast.BitOr): return _int_exact(left) | _int_exact(right);
        if isinstance(node.op, ast.BitXor): return _int_exact(left) ^ _int_exact(right);
        raise SumArithmeticError("unsupported binary operator");

    def visit_BoolOp(self, node):
        if isinstance(node.op, ast.And):
            result = 1;
            for value in node.values:
                result = self.visit(value);
                if not bool(result): return 0;
            return 1 if bool(result) else 0;
        if isinstance(node.op, ast.Or):
            for value in node.values:
                result = self.visit(value);
                if bool(result): return 1;
            return 0;
        raise SumArithmeticError("unsupported boolean operator");

    def visit_Compare(self, node):
        left = self.visit(node.left);
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator);
            if isinstance(op, ast.Eq): ok = left == right;
            elif isinstance(op, ast.NotEq): ok = left != right;
            elif isinstance(op, ast.Lt): ok = left < right;
            elif isinstance(op, ast.LtE): ok = left <= right;
            elif isinstance(op, ast.Gt): ok = left > right;
            elif isinstance(op, ast.GtE): ok = left >= right;
            else: raise SumArithmeticError("unsupported comparison operator");
            if not ok: return 0;
            left = right;
        return 1;

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise SumArithmeticError("unsupported arithmetic function");
        if node.keywords: raise SumArithmeticError("keyword arguments are not supported");
        return _FUNCTIONS[node.func.id](*[self.visit(arg) for arg in node.args]);

    def generic_visit(self, node):
        raise SumArithmeticError("unsupported arithmetic syntax: {}".format(type(node).__name__));


def evaluate(expression, variables=None):
    text = str(expression).strip();
    text = text.replace("&&", " and ").replace("||", " or ");
    # Bash uses ! for boolean negation. Avoid touching !=.
    out = []; i = 0;
    while i < len(text):
        if text[i] == "!" and (i + 1 >= len(text) or text[i + 1] != "="):
            out.append(" not "); i += 1; continue;
        out.append(text[i]); i += 1;
    text = "".join(out);
    try: tree = ast.parse(text, mode="eval");
    except SyntaxError as exc: raise SumArithmeticError(str(exc)) from exc;
    try: return _Evaluator(variables).visit(tree);
    except ZeroDivisionError as exc: raise SumArithmeticError("division by zero") from exc;

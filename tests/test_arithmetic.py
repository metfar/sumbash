from sumbash.arithmetic import SumArithmeticError, evaluate, format_number;


def calc(expr): return format_number(evaluate(expr));


def test_fractional_arithmetic_is_native():
    assert calc("5.5 * 2") == "11";
    assert calc("5 / 2") == "2.5";
    assert calc("5 // 2") == "2";


def test_math_functions_follow_sum_semantics():
    assert calc("int(5.5)") == "5";
    assert calc("int(-5.5)") == "-5";
    assert calc("round(5.5)") == "6";
    assert calc("round(-5.5)") == "-6";
    assert calc("floor(-5.5)") == "-6";
    assert calc("ceil(-5.5)") == "-5";
    assert calc("sqrt(9)") == "3";
    assert calc("pow(2, 8)") == "256";


def test_bitwise_rejects_fractional_operands():
    try: evaluate("5.5 & 3");
    except SumArithmeticError: pass;
    else: raise AssertionError("fractional bitwise operand should fail");

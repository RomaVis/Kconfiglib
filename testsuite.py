# Copyright (c) 2011-2018, Ulf Magnusson
# SPDX-License-Identifier: ISC

# This is the Kconfiglib test suite. It runs selftests on Kconfigs provided by
# us and tests compatibility with the C Kconfig implementation by comparing the
# output of Kconfiglib with the output of the scripts/kconfig/*conf utilities
# for different targets and defconfigs. It should be run from the top-level
# kernel directory with
#
#   $ python Kconfiglib/testsuite.py
#
# Some additional options can be turned on by passing them as arguments. They
# default to off.
#
#  - speedy:
#    Run scripts/kconfig/conf directly instead of using 'make' targets. Makes
#    things a lot faster, but could break if Kconfig files start referencing
#    additional environment variables beyond ARCH, SRCARCH, and KERNELVERSION.
#    Safe as of Linux 4.14-rc3.
#
#  - obsessive:
#    By default, only valid arch/defconfig pairs are tested. In obsessive mode,
#    every arch will be tested with every defconfig. Increases the testing time
#    by an order of magnitude. Occasionally finds (usually obscure) bugs, and I
#    make sure everything passes with it.
#
#  - log:
#    Log timestamped defconfig test failures to the file test_defconfig_fails.
#    Handy in obsessive mode.
#
# For example, this commands runs the test suite in speedy mode with logging
# enabled:
#
#   $ python(3) Kconfiglib/testsuite.py speedy log
#
# pypy works too, and runs most tests much faster than CPython.
#
# All tests should pass. Report regressions to ulfalizer a.t Google's email
# service.

from kconfiglib import Kconfig, Symbol, Choice, COMMENT, MENU, \
                       BOOL, TRISTATE, HEX, STRING, \
                       TRI_TO_STR, \
                       KconfigSyntaxError, expr_value, escape, unescape
import difflib
import errno
import os
import platform
import re
import subprocess
import sys
import textwrap
import time

def shell(cmd):
    with open(os.devnull, "w") as devnull:
        subprocess.call(cmd, shell=True, stdout=devnull, stderr=devnull)

all_passed = True

def fail(msg=None):
    global all_passed
    all_passed = False
    if msg is not None:
        print("fail: " + msg)

def verify(cond, msg):
    if not cond:
        fail(msg)

def verify_equal(x, y):
    if x != y:
        fail("'{}' does not equal '{}'".format(x, y))

# Referenced inside the kernel Kconfig files.
#
# The str() makes the type of the value 'str' on both Python 2 and Python 3,
# which is nice for some later dictionary key sanity checks.
os.environ["KERNELVERSION"] = str(
    subprocess.check_output(("make", "kernelversion")).decode("utf-8").rstrip()
)

# Prevent accidental loading of configuration files by removing
# KCONFIG_ALLCONFIG from the environment
os.environ.pop("KCONFIG_ALLCONFIG", None)

speedy = False
obsessive = False
log = False

# Number of arch/defconfig pairs tested so far
nconfigs = 0

def run_tests():
    global speedy, obsessive, log
    for s in sys.argv[1:]:
        if s == "speedy":
            speedy = True
            print("Speedy mode enabled")
        elif s == "obsessive":
            obsessive = True
            print("Obsessive mode enabled")
        elif s == "log":
            log = True
            print("Log mode enabled")
        else:
            print("Unrecognized option '{}'".format(s))
            return

    run_selftests()
    run_compatibility_tests()

def run_selftests():
    #
    # Common helper functions. These all expect 'c' to hold the current
    # configuration.
    #

    def verify_value(sym_name, val):
        """
        Verifies that a symbol has a particular value.
        """
        if isinstance(val, int):
            val = TRI_TO_STR[val]

        sym = c.syms[sym_name]
        verify(sym.str_value == val,
               'expected {} to have the value "{}", had the value "{}"'
               .format(sym_name, val, sym.str_value))

    def assign_and_verify_value(sym_name, val, new_val):
        """
        Assigns 'val' to a symbol and verifies that its value becomes
        'new_val'. Assumes (and tests) that 'val' is valid for the
        symbol type.
        """
        if isinstance(new_val, int):
            new_val = TRI_TO_STR[new_val]

        sym = c.syms[sym_name]
        old_val = sym.str_value
        verify(sym.set_value(val),
               "assigning '{}' to {} unexpectedly failed"
               .format(val, sym_name))
        verify(sym.str_value == new_val,
               "expected {} to have the value '{}' after being assigned the "
               "value '{}'. Instead, the value is '{}'. The old value was "
               "'{}'."
               .format(sym_name, new_val, val, sym.str_value, old_val))

    def assign_and_verify(sym_name, user_val):
        """
        Like assign_and_verify_value(), with the expected value being the
        value just set.
        """
        assign_and_verify_value(sym_name, user_val, user_val)

    def assign_and_verify_user_value(sym_name, val, user_val, valid):
        """
        Assigns a user value to the symbol and verifies the new user value. If
        valid is True, the user value is valid for the type, otherwise not.
        This is used to test the set_value() return value.
        """
        sym = c.syms[sym_name]
        sym_old_user_val = sym.user_value

        verify(sym.set_value(val) == valid,
               "expected the user value '{}' to be {} for {}, was not"
               .format(val, "valid" if valid else "invalid", sym_name))
        verify(sym.user_value == user_val,
               "the assigned user value '{}' wasn't reflected in user_value "
               "on the symbol {}. Instead, the new user_value was '{}'. The "
               "old user value was '{}'."
               .format(user_val, sym_name, sym.user_value, sym_old_user_val))

    #
    # Selftests
    #

    print("Testing string literal lexing")

    # Dummy empty configuration just to get a Kconfig object
    c = Kconfig("Kconfiglib/tests/empty")

    def verify_string_lex(s, res):
        """
        Verifies that a constant symbol with the name 'res' is produced from
        lexing 's'
        """
        c.eval_string(s)
        verify(c._tokens[0].name == res,
               "expected <{}> to produced the constant symbol <{}>, "
               'produced <{}>'.format(s[1:-1], c._tokens[0].name, res))

    verify_string_lex(r""" "" """, "")
    verify_string_lex(r""" '' """, "")

    verify_string_lex(r""" "a" """, "a")
    verify_string_lex(r""" 'a' """, "a")
    verify_string_lex(r""" "ab" """, "ab")
    verify_string_lex(r""" 'ab' """, "ab")
    verify_string_lex(r""" "abc" """, "abc")
    verify_string_lex(r""" 'abc' """, "abc")

    verify_string_lex(r""" "'" """, "'")
    verify_string_lex(r""" '"' """, '"')

    verify_string_lex(r""" "\"" """, '"')
    verify_string_lex(r""" '\'' """, "'")

    verify_string_lex(r""" "\"\"" """, '""')
    verify_string_lex(r""" '\'\'' """, "''")

    verify_string_lex(r""" "\'" """, "'")
    verify_string_lex(r""" '\"' """, '"')

    verify_string_lex(r""" "\\" """, "\\")
    verify_string_lex(r""" '\\' """, "\\")

    verify_string_lex(r""" "\a\\'\b\c\"'d" """, 'a\\\'bc"\'d')
    verify_string_lex(r""" '\a\\"\b\c\'"d' """, "a\\\"bc'\"d")

    def verify_string_bad(s):
        """
        Verifies that tokenizing 's' throws a KconfigSyntaxError. Strips the
        first and last characters from 's' so we can use readable raw strings
        as input.
        """
        try:
            c.eval_string(s)
        except KconfigSyntaxError:
            pass
        else:
            fail("expected tokenization of {} to fail, didn't".format(s[1:-1]))

    verify_string_bad(r""" " """)
    verify_string_bad(r""" ' """)
    verify_string_bad(r""" "' """)
    verify_string_bad(r""" '" """)
    verify_string_bad(r""" "\" """)
    verify_string_bad(r""" '\' """)
    verify_string_bad(r""" "foo """)
    verify_string_bad(r""" 'foo """)


    print("Testing escape() and unescape()")

    def verify_escape_unescape(s, sesc):
        # Verify that 's' escapes to 'sesc' and that 'sesc' unescapes to 's'
        verify_equal(escape(s), sesc)
        verify_equal(unescape(sesc), s)

    verify_escape_unescape(r''          , r''              )
    verify_escape_unescape(r'foo'       , r'foo'           )
    verify_escape_unescape(r'"'         , r'\"'            )
    verify_escape_unescape(r'""'        , r'\"\"'          )
    verify_escape_unescape('\\'         , r'\\'            )
    verify_escape_unescape(r'\\'        , r'\\\\'          )
    verify_escape_unescape(r'\"'        , r'\\\"'          )
    verify_escape_unescape(r'"ab\cd"ef"', r'\"ab\\cd\"ef\"')

    # Backslashes before any character should be unescaped, not just before "
    # and \
    verify_equal(unescape(r"\afoo\b\c\\d\\\e\\\\f"), r"afoobc\d\e\\f")


    print("Testing expression evaluation")

    c = Kconfig("Kconfiglib/tests/Keval")

    def verify_eval(expr, val):
        res = c.eval_string(expr)
        verify(res == val,
               "'{}' evaluated to {}, expected {}".format(expr, res, val))

    # No modules
    verify_eval("n", 0)
    verify_eval("m", 0)
    verify_eval("y", 2)
    verify_eval("'n'", 0)
    verify_eval("'m'", 0)
    verify_eval("'y'", 2)
    verify_eval("M", 2)

    # Modules
    c.modules.set_value(2)
    verify_eval("n", 0)
    verify_eval("m", 1)
    verify_eval("y", 2)
    verify_eval("'n'", 0)
    verify_eval("'m'", 1)
    verify_eval("'y'", 2)
    verify_eval("M", 1)
    verify_eval("(Y || N) && (m && y)", 1)

    # Non-bool/non-tristate symbols are always n in a tristate sense
    verify_eval("Y_STRING", 0)
    verify_eval("Y_STRING || m", 1)

    # As are all constants besides y and m
    verify_eval('"foo"', 0)
    verify_eval('"foo" || "bar"', 0)
    verify_eval('"foo" || m', 1)

    # Test equality for symbols

    verify_eval("N = N", 2)
    verify_eval("N = n", 2)
    verify_eval("N = 'n'", 2)
    verify_eval("N != N", 0)
    verify_eval("N != n", 0)
    verify_eval("N != 'n'", 0)

    verify_eval("M = M", 2)
    verify_eval("M = m", 2)
    verify_eval("M = 'm'", 2)
    verify_eval("M != M", 0)
    verify_eval("M != m", 0)
    verify_eval("M != 'm'", 0)

    verify_eval("Y = Y", 2)
    verify_eval("Y = y", 2)
    verify_eval("Y = 'y'", 2)
    verify_eval("Y != Y", 0)
    verify_eval("Y != y", 0)
    verify_eval("Y != 'y'", 0)

    verify_eval("N != M", 2)
    verify_eval("N != Y", 2)
    verify_eval("M != Y", 2)

    verify_eval("Y_STRING = y", 2)
    verify_eval("Y_STRING = 'y'", 2)
    verify_eval('FOO_BAR_STRING = "foo bar"', 2)
    verify_eval('FOO_BAR_STRING != "foo bar baz"', 2)
    verify_eval('INT_37 = 37', 2)
    verify_eval("INT_37 = '37'", 2)
    verify_eval('HEX_0X37 = 0x37', 2)
    verify_eval("HEX_0X37 = '0x37'", 2)

    # These should also hold after 31847b67 (kconfig: allow use of relations
    # other than (in)equality)
    verify_eval("HEX_0X37 = '0x037'", 2)
    verify_eval("HEX_0X37 = '0x0037'", 2)

    # Constant symbol comparisons
    verify_eval('"foo" != "bar"', 2)
    verify_eval('"foo" = "bar"', 0)
    verify_eval('"foo" = "foo"', 2)

    # Undefined symbols get their name as their value
    c.disable_warnings()
    verify_eval("'not_defined' = not_defined", 2)
    verify_eval("not_defined_2 = not_defined_2", 2)
    verify_eval("not_defined_1 != not_defined_2", 2)

    # Test less than/greater than

    # Basic evaluation
    verify_eval("INT_37 < 38", 2)
    verify_eval("38 < INT_37", 0)
    verify_eval("INT_37 < '38'", 2)
    verify_eval("'38' < INT_37", 0)
    verify_eval("INT_37 < 138", 2)
    verify_eval("138 < INT_37", 0)
    verify_eval("INT_37 < '138'", 2)
    verify_eval("'138' < INT_37", 0)
    verify_eval("INT_37 < -138", 0)
    verify_eval("-138 < INT_37", 2)
    verify_eval("INT_37 < '-138'", 0)
    verify_eval("'-138' < INT_37", 2)
    verify_eval("INT_37 < 37", 0)
    verify_eval("37 < INT_37", 0)
    verify_eval("INT_37 < 36", 0)
    verify_eval("36 < INT_37", 2)

    # Different formats in comparison
    verify_eval("INT_37 < 0x26", 2) # 38
    verify_eval("INT_37 < 0x25", 0) # 37
    verify_eval("INT_37 < 0x24", 0) # 36
    verify_eval("HEX_0X37 < 56", 2) # 0x38
    verify_eval("HEX_0X37 < 55", 0) # 0x37
    verify_eval("HEX_0X37 < 54", 0) # 0x36

    # Other int comparisons
    verify_eval("INT_37 <= 38", 2)
    verify_eval("INT_37 <= 37", 2)
    verify_eval("INT_37 <= 36", 0)
    verify_eval("INT_37 >  38", 0)
    verify_eval("INT_37 >  37", 0)
    verify_eval("INT_37 >  36", 2)
    verify_eval("INT_37 >= 38", 0)
    verify_eval("INT_37 >= 37", 2)
    verify_eval("INT_37 >= 36", 2)

    # Other hex comparisons
    verify_eval("HEX_0X37 <= 0x38", 2)
    verify_eval("HEX_0X37 <= 0x37", 2)
    verify_eval("HEX_0X37 <= 0x36", 0)
    verify_eval("HEX_0X37 >  0x38", 0)
    verify_eval("HEX_0X37 >  0x37", 0)
    verify_eval("HEX_0X37 >  0x36", 2)
    verify_eval("HEX_0X37 >= 0x38", 0)
    verify_eval("HEX_0X37 >= 0x37", 2)
    verify_eval("HEX_0X37 >= 0x36", 2)

    # A hex holding a value without a "0x" prefix should still be treated as
    # hexadecimal
    verify_eval("HEX_37 < 0x38", 2)
    verify_eval("HEX_37 < 0x37", 0)
    verify_eval("HEX_37 < 0x36", 0)

    # Symbol comparisons
    verify_eval("INT_37   <  HEX_0X37", 2)
    verify_eval("INT_37   >  HEX_0X37", 0)
    verify_eval("HEX_0X37 <  INT_37  ", 0)
    verify_eval("HEX_0X37 >  INT_37  ", 2)
    verify_eval("INT_37   <  INT_37  ", 0)
    verify_eval("INT_37   <= INT_37  ", 2)
    verify_eval("INT_37   >  INT_37  ", 0)
    verify_eval("INT_37   <= INT_37  ", 2)

    # Tristate value comparisons
    verify_eval("n < n", 0)
    verify_eval("n < m", 2)
    verify_eval("n < y", 2)
    verify_eval("n < N", 0)
    verify_eval("n < M", 2)
    verify_eval("n < Y", 2)
    verify_eval("0 > n", 0)
    verify_eval("1 > n", 2)
    verify_eval("2 > n", 2)
    verify_eval("m < n", 0)
    verify_eval("m < m", 0)
    verify_eval("m < y", 2)

    # Strings compare lexicographically
    verify_eval("'aa' < 'ab'", 2)
    verify_eval("'aa' > 'ab'", 0)
    verify_eval("'ab' < 'aa'", 0)
    verify_eval("'ab' > 'aa'", 2)

    # Comparisons where one of the operands doesn't parse as a number also give
    # a lexicographic comparison
    verify_eval("INT_37 <  '37a' ", 2)
    verify_eval("'37a'  >  INT_37", 2)
    verify_eval("INT_37 <= '37a' ", 2)
    verify_eval("'37a'  >= INT_37", 2)
    verify_eval("INT_37 >= '37a' ", 0)
    verify_eval("INT_37 >  '37a' ", 0)
    verify_eval("'37a'  <  INT_37", 0)
    verify_eval("'37a'  <= INT_37", 0)

    def verify_eval_bad(expr):
        try:
            c.eval_string(expr)
        except KconfigSyntaxError:
            pass
        else:
            fail('expected eval_string("{}") to throw KconfigSyntaxError, '
                 "didn't".format(expr))

    # The C implementation's parser can be pretty lax about syntax. Kconfiglib
    # sometimes needs to emulate that. Verify that some bad stuff throws
    # KconfigSyntaxError at least.
    verify_eval_bad("")
    verify_eval_bad("&")
    verify_eval_bad("|")
    verify_eval_bad("!")
    verify_eval_bad("(")
    verify_eval_bad(")")
    verify_eval_bad("=")
    verify_eval_bad("(X")
    verify_eval_bad("X &&")
    verify_eval_bad("&& X")
    verify_eval_bad("X ||")
    verify_eval_bad("|| X")


    print("Testing Symbol.__str__()")

    def verify_str(item, s):
        verify_equal(str(item), s[1:])

    c = Kconfig("Kconfiglib/tests/Kstr", warn=False)

    c.modules.set_value(2)

    verify_str(c.syms["UNDEFINED"], """
""")

    verify_str(c.syms["BASIC_NO_PROMPT"], """
config BASIC_NO_PROMPT
	bool
	help
	  blah blah
	  
	    blah blah blah
	  
	   blah
""")

    verify_str(c.syms["BASIC_PROMPT"], """
config BASIC_PROMPT
	bool
	prompt "basic"
""")

    verify_str(c.syms["ADVANCED"], """
config ADVANCED
	tristate
	prompt "prompt" if DEP
	default DEFAULT_1
	default DEFAULT_2 if DEP
	select SELECTED_1
	select SELECTED_2 if DEP
	imply IMPLIED_1
	imply IMPLIED_2 if DEP
	help
	  first help text

config ADVANCED
	prompt "prompt 2"

menuconfig ADVANCED
	prompt "prompt 3" if DEP2
	depends on DEP2

config ADVANCED
	depends on DEP4 && DEP3
	help
	  second help text
""")

    verify_str(c.syms["ONLY_DIRECT_DEPS"], """
config ONLY_DIRECT_DEPS
	int
	depends on DEP1 && DEP2
""")

    verify_str(c.syms["STRING"], """
config STRING
	string
	default "foo"
	default "bar" if DEP
	default STRING2
	default STRING3 if DEP
""")

    verify_str(c.syms["INT"], """
config INT
	int
	range 1 2
	range FOO BAR
	range BAZ QAZ if DEP
""")

    verify_str(c.modules, """
config MODULES
	bool
	prompt "MODULES"
	option modules
""")

    verify_str(c.syms["OPTIONS"], """
config OPTIONS
	option allnoconfig_y
	option defconfig_list
	option env="ENV"
""")


    print("Testing Choice.__str__()")

    verify_str(c.named_choices["CHOICE"], """
choice CHOICE
	tristate
	prompt "foo"
	default CHOICE_1
	default CHOICE_2 if dep
""")

    verify_str(c.named_choices["CHOICE"].nodes[0].next.item, """
choice
	tristate
	prompt "no name"
	optional
""")


    print("Testing Symbol.__repr__()")

    def verify_repr(item, s):
        verify_equal(repr(item) + "\n", s[1:])

    c = Kconfig("Kconfiglib/tests/Krepr", warn=False)

    verify_repr(c.n, """
<symbol n, tristate, value n, constant>
""")

    verify_repr(c.m, """
<symbol m, tristate, value m, constant>
""")

    verify_repr(c.y, """
<symbol y, tristate, value y, constant>
""")

    verify_repr(c.syms["UNDEFINED"], """
<symbol UNDEFINED, unknown, value "UNDEFINED", visibility n, direct deps n, undefined>
""")

    verify_repr(c.syms["BASIC"], """
<symbol BASIC, bool, value y, visibility n, direct deps y, Kconfiglib/tests/Krepr:9>
""")

    verify_repr(c.syms["VISIBLE"], """
<symbol VISIBLE, bool, "visible", value n, visibility y, direct deps y, Kconfiglib/tests/Krepr:14>
""")

    c.syms["VISIBLE"].set_value(2)

    verify_repr(c.syms["VISIBLE"], """
<symbol VISIBLE, bool, "visible", value y, user value y, visibility y, direct deps y, Kconfiglib/tests/Krepr:14>
""")

    verify_repr(c.syms["DIR_DEP_N"], """
<symbol DIR_DEP_N, unknown, value "DIR_DEP_N", visibility n, direct deps n, Kconfiglib/tests/Krepr:17>
""")

    verify_repr(c.syms["OPTIONS"], """
<symbol OPTIONS, unknown, value "OPTIONS", visibility n, allnoconfig_y, is the defconfig_list symbol, from environment variable ENV, direct deps y, Kconfiglib/tests/Krepr:20>
""")

    verify_repr(c.syms["MULTI_DEF"], """
<symbol MULTI_DEF, unknown, value "MULTI_DEF", visibility n, direct deps y, Kconfiglib/tests/Krepr:25, Kconfiglib/tests/Krepr:26>
""")

    verify_repr(c.syms["CHOICE_1"], """
<symbol CHOICE_1, tristate, "choice sym", value n, visibility m, choice symbol, direct deps m, Kconfiglib/tests/Krepr:33>
""")

    verify_repr(c.modules, """
<symbol MODULES, bool, value y, visibility n, is the modules symbol, direct deps y, Kconfiglib/tests/Krepr:1>
""")


    print("Testing Choice.__repr__()")

    verify_repr(c.named_choices["CHOICE"], """
<choice CHOICE, tristate, "choice", mode m, visibility y, Kconfiglib/tests/Krepr:30>
""")

    c.named_choices["CHOICE"].set_value(2)

    verify_repr(c.named_choices["CHOICE"], """
<choice CHOICE, tristate, "choice", mode y, user mode y, CHOICE_1 selected, visibility y, Kconfiglib/tests/Krepr:30>
""")

    c.syms["CHOICE_2"].set_value(2)

    verify_repr(c.named_choices["CHOICE"], """
<choice CHOICE, tristate, "choice", mode y, user mode y, CHOICE_2 selected, CHOICE_2 selected by user, visibility y, Kconfiglib/tests/Krepr:30>
""")

    c.named_choices["CHOICE"].set_value(1)

    verify_repr(c.named_choices["CHOICE"], """
<choice CHOICE, tristate, "choice", mode m, user mode m, CHOICE_2 selected by user (overridden), visibility y, Kconfiglib/tests/Krepr:30>
""")

    verify_repr(c.syms["CHOICE_HOOK"].nodes[0].next.item, """
<choice, tristate, "optional choice", mode n, visibility n, optional, Kconfiglib/tests/Krepr:43>
""")


    print("Testing MenuNode.__repr__()")

    verify_repr(c.syms["BASIC"].nodes[0], """
<menu node for symbol BASIC, deps y, has help, has next, Kconfiglib/tests/Krepr:9>
""")

    verify_repr(c.syms["DIR_DEP_N"].nodes[0], """
<menu node for symbol DIR_DEP_N, deps n, has next, Kconfiglib/tests/Krepr:17>
""")

    verify_repr(c.syms["MULTI_DEF"].nodes[0], """
<menu node for symbol MULTI_DEF, deps y, has next, Kconfiglib/tests/Krepr:25>
""")

    verify_repr(c.syms["MULTI_DEF"].nodes[1], """
<menu node for symbol MULTI_DEF, deps y, has next, Kconfiglib/tests/Krepr:26>
""")

    verify_repr(c.syms["MENUCONFIG"].nodes[0], """
<menu node for symbol MENUCONFIG, is menuconfig, deps y, has next, Kconfiglib/tests/Krepr:28>
""")

    verify_repr(c.named_choices["CHOICE"].nodes[0], """
<menu node for choice CHOICE, prompt "choice" (visibility y), deps y, has child, has next, Kconfiglib/tests/Krepr:30>
""")

    verify_repr(c.syms["CHOICE_HOOK"].nodes[0].next, """
<menu node for choice, prompt "optional choice" (visibility n), deps y, has next, Kconfiglib/tests/Krepr:43>
""")

    verify_repr(c.syms["NO_VISIBLE_IF_HOOK"].nodes[0].next, """
<menu node for menu, prompt "no visible if" (visibility y), deps y, 'visible if' deps y, has next, Kconfiglib/tests/Krepr:50>
""")

    verify_repr(c.syms["VISIBLE_IF_HOOK"].nodes[0].next, """
<menu node for menu, prompt "visible if" (visibility y), deps y, 'visible if' deps m, has next, Kconfiglib/tests/Krepr:55>
""")

    verify_repr(c.syms["COMMENT_HOOK"].nodes[0].next, """
<menu node for comment, prompt "comment" (visibility y), deps y, Kconfiglib/tests/Krepr:61>
""")


    print("Testing Kconfig.__repr__()")

    verify_repr(c, """
<configuration with 15 symbols, main menu prompt "Linux Kernel Configuration", srctree not set, config symbol prefix "CONFIG_", warnings disabled, undef. symbol assignment warnings disabled, redundant symbol assignment warnings enabled>
""")

    os.environ["srctree"] = "srctree value"
    os.environ["CONFIG_"] = "CONFIG_ value"

    c = Kconfig("Kconfiglib/tests/Krepr", warn=False)
    c.enable_warnings()
    c.disable_redun_warnings()
    c.enable_undef_warnings()

    verify_repr(c, """
<configuration with 15 symbols, main menu prompt "Linux Kernel Configuration", srctree "srctree value", config symbol prefix "CONFIG_ value", warnings enabled, undef. symbol assignment warnings enabled, redundant symbol assignment warnings disabled>
""")

    os.environ.pop("srctree", None)
    os.environ.pop("CONFIG_", None)


    print("Testing tricky help strings")

    c = Kconfig("Kconfiglib/tests/Khelp")

    def verify_help(node, s):
        verify_equal(node.help, s[1:])

    verify_help(c.syms["TWO_HELP_STRINGS"].nodes[0], """
first help string
""")

    verify_help(c.syms["TWO_HELP_STRINGS"].nodes[1], """
second help string
""")

    verify_help(c.syms["NO_BLANK_AFTER_HELP"].nodes[0], """
help for
NO_BLANK_AFTER_HELP
""")

    verify_help(c.named_choices["CHOICE_HELP"].nodes[0], """
help for
CHOICE_HELP
""")

    verify_help(c.syms["HELP_TERMINATED_BY_COMMENT"].nodes[0], """
a
b
c
""")

    verify_help(c.syms["TRICKY_HELP"].nodes[0], """
a
 b
  c

 d
  e
   f


g
 h
  i
""")


    print("Testing locations and 'source', 'rsource'")

    def verify_locations(nodes, *expected_locs):
        verify(len(nodes) == len(expected_locs),
               "Wrong number of locations for " + repr(nodes))

        for node, expected_loc in zip(nodes, expected_locs):
            node_loc = "{}:{}".format(node.filename, node.linenr)
            verify(node_loc == expected_loc,
                   "expected {} to have the location {}, had the location {}"
                   .format(repr(node), expected_loc, node_loc))

    # Expanded in the 'source' statement in Klocation
    os.environ["TESTS_DIR_FROM_ENV"] = "tests"
    os.environ["SUB_DIR_FROM_ENV"] = "sub"
    os.environ["srctree"] = "Kconfiglib/"

    # Has symbol with empty help text, so disable warnings
    c = Kconfig("tests/Klocation", warn=False)

    os.environ.pop("TESTS_DIR_FROM_ENV", None)
    os.environ.pop("SUB_DIR_FROM_ENV", None)
    os.environ.pop("srctree", None)

    verify_locations(c.syms["SINGLE_DEF"].nodes, "tests/Klocation:4")

    verify_locations(c.syms["MULTI_DEF"].nodes,
      "tests/Klocation:7",
      "tests/Klocation:31",
      "tests/Klocation_sourced:3",
      "tests/sub/Klocation_rsourced:2",
      "tests/Klocation:57")

    verify_locations(c.named_choices["CHOICE"].nodes,
                     "tests/Klocation_sourced:5")

    verify_locations([c.syms["MENU_HOOK"].nodes[0].next],
                     "tests/Klocation_sourced:12")

    verify_locations([c.syms["COMMENT_HOOK"].nodes[0].next],
                     "tests/Klocation_sourced:18")

    # Test recursive 'source' detection

    try:
        Kconfig("Kconfiglib/tests/Krecursive1")
    except KconfigSyntaxError:
        pass
    except:
        fail("recursive 'source' raised wrong exception")
    else:
        fail("recursive 'source' did not raise exception")


    print("Testing visibility")

    c = Kconfig("Kconfiglib/tests/Kvisibility")

    def verify_visibility(item, no_module_vis, module_vis):
        c.modules.set_value(0)
        verify(item.visibility == no_module_vis,
               "expected {} to have visibility {} without modules, had "
               "visibility {}".
               format(repr(item), no_module_vis, item.visibility))

        c.modules.set_value(2)
        verify(item.visibility == module_vis,
               "expected {} to have visibility {} with modules, had "
               "visibility {}".
               format(repr(item), module_vis, item.visibility))

    # Symbol visibility

    verify_visibility(c.syms["NO_PROMPT"],     0, 0)
    verify_visibility(c.syms["BOOL_N"],        0, 0)
    verify_visibility(c.syms["BOOL_M"],        0, 2)
    verify_visibility(c.syms["BOOL_MOD"],      2, 2)
    verify_visibility(c.syms["BOOL_Y"],        2, 2)
    verify_visibility(c.syms["TRISTATE_M"],    0, 1)
    verify_visibility(c.syms["TRISTATE_MOD"],  2, 1)
    verify_visibility(c.syms["TRISTATE_Y"],    2, 2)
    verify_visibility(c.syms["BOOL_IF_N"],     0, 0)
    verify_visibility(c.syms["BOOL_IF_M"],     0, 2)
    verify_visibility(c.syms["BOOL_IF_Y"],     2, 2)
    verify_visibility(c.syms["BOOL_MENU_N"],   0, 0)
    verify_visibility(c.syms["BOOL_MENU_M"],   0, 2)
    verify_visibility(c.syms["BOOL_MENU_Y"],   2, 2)
    verify_visibility(c.syms["BOOL_CHOICE_N"], 0, 0)

    # Non-tristate symbols in tristate choices are only visible if the choice
    # is in y mode

    # The choice can't be brought to y mode because of the 'if m'
    verify_visibility(c.syms["BOOL_CHOICE_M"], 0, 0)
    c.syms["BOOL_CHOICE_M"].choice.set_value(2)
    verify_visibility(c.syms["BOOL_CHOICE_M"], 0, 0)

    # The choice gets y mode only when running without modules, because it
    # defaults to m mode
    verify_visibility(c.syms["BOOL_CHOICE_Y"], 2, 0)
    c.syms["BOOL_CHOICE_Y"].choice.set_value(2)
    # When set to y mode, the choice symbol becomes visible both with and
    # without modules
    verify_visibility(c.syms["BOOL_CHOICE_Y"], 2, 2)

    verify_visibility(c.syms["TRISTATE_IF_N"],     0, 0)
    verify_visibility(c.syms["TRISTATE_IF_M"],     0, 1)
    verify_visibility(c.syms["TRISTATE_IF_Y"],     2, 2)
    verify_visibility(c.syms["TRISTATE_MENU_N"],   0, 0)
    verify_visibility(c.syms["TRISTATE_MENU_M"],   0, 1)
    verify_visibility(c.syms["TRISTATE_MENU_Y"],   2, 2)
    verify_visibility(c.syms["TRISTATE_CHOICE_N"], 0, 0)
    verify_visibility(c.syms["TRISTATE_CHOICE_M"], 0, 1)
    verify_visibility(c.syms["TRISTATE_CHOICE_Y"], 2, 2)

    verify_visibility(c.named_choices["BOOL_CHOICE_N"],     0, 0)
    verify_visibility(c.named_choices["BOOL_CHOICE_M"],     0, 2)
    verify_visibility(c.named_choices["BOOL_CHOICE_Y"],     2, 2)
    verify_visibility(c.named_choices["TRISTATE_CHOICE_N"], 0, 0)
    verify_visibility(c.named_choices["TRISTATE_CHOICE_M"], 0, 1)
    verify_visibility(c.named_choices["TRISTATE_CHOICE_Y"], 2, 2)

    verify_visibility(c.named_choices["TRISTATE_CHOICE_IF_M_AND_Y"],   0, 1)
    verify_visibility(c.named_choices["TRISTATE_CHOICE_MENU_N_AND_Y"], 0, 0)

    # Verify that 'visible if' visibility gets propagated to prompts

    verify_visibility(c.syms["VISIBLE_IF_N"], 0, 0)
    verify_visibility(c.syms["VISIBLE_IF_M"], 0, 1)
    verify_visibility(c.syms["VISIBLE_IF_Y"], 2, 2)
    verify_visibility(c.syms["VISIBLE_IF_M_2"], 0, 1)

    # Verify that string/int/hex symbols with m visibility accept a user value

    assign_and_verify("STRING_m", "foo bar")
    assign_and_verify("INT_m", "123")
    assign_and_verify("HEX_m", "0x123")


    print("Testing .assignable")

    c = Kconfig("Kconfiglib/tests/Kassignable")

    def verify_assignable_imp(item, assignable_no_modules, assignable_modules):
        """
        Verifies the assignable values for 'item', with and without modules.
        """
        for modules_val, assignable in (0, assignable_no_modules), \
                                       (2, assignable_modules):

            c.modules.set_value(modules_val)
            module_msg = "without modules" if modules_val == 0 else \
                         "with modules"

            verify(item.assignable == assignable,
                   "Incorrect assignable values for {} {}. Should be {}, "
                   "was {}."
                   .format(item.name, module_msg, assignable, item.assignable))

            # Verify that the values can actually be assigned too

            for val in item.assignable:
                item.set_value(val)
                verify(item.tri_value == val,
                       "Unable to set {} to {} {}, even though it was in "
                       ".assignable".format(item.name, val, module_msg))

    def verify_assignable(sym_name, assignable_no_modules, assignable_modules):
        verify_assignable_imp(c.syms[sym_name],
                              assignable_no_modules,
                              assignable_modules)

    def verify_const_unassignable(sym_name):
        verify_assignable_imp(c.const_syms[sym_name], (), ())

    # Things that shouldn't be .assignable
    verify_const_unassignable("n")
    verify_const_unassignable("m")
    verify_const_unassignable("y")
    verify_const_unassignable("const")
    verify_const_unassignable("UNAME_RELEASE")
    verify_assignable("UNDEFINED", (), ())
    verify_assignable("NO_PROMPT", (), ())
    verify_assignable("STRING", (), ())
    verify_assignable("INT", (), ())
    verify_assignable("HEX", (), ())

    # Non-selected symbols
    verify_assignable("Y_VIS_BOOL", (0, 2), (0,    2))
    verify_assignable("M_VIS_BOOL", (    ), (0,    2))  # Vis. promoted
    verify_assignable("N_VIS_BOOL", (    ), (       ))
    verify_assignable("Y_VIS_TRI",  (0, 2), (0, 1, 2))
    verify_assignable("M_VIS_TRI",  (    ), (0, 1   ))
    verify_assignable("N_VIS_TRI",  (    ), (       ))

    # Symbols selected to y
    verify_assignable("Y_SEL_Y_VIS_BOOL", (2,), (2,))
    verify_assignable("Y_SEL_M_VIS_BOOL", (  ), (2,))  # Vis. promoted
    verify_assignable("Y_SEL_N_VIS_BOOL", (  ), (  ))
    verify_assignable("Y_SEL_Y_VIS_TRI",  (2,), (2,))
    verify_assignable("Y_SEL_M_VIS_TRI",  (  ), (2,))
    verify_assignable("Y_SEL_N_VIS_TRI",  (  ), (  ))

    # Symbols selected to m
    verify_assignable("M_SEL_Y_VIS_BOOL", (2,), (   2,))  # Value promoted
    verify_assignable("M_SEL_M_VIS_BOOL", (  ), (   2,))  # Vis./value promoted
    verify_assignable("M_SEL_N_VIS_BOOL", (  ), (     ))
    verify_assignable("M_SEL_Y_VIS_TRI",  (2,), (1, 2 ))
    verify_assignable("M_SEL_M_VIS_TRI",  (  ), (1,   ))
    verify_assignable("M_SEL_N_VIS_TRI",  (  ), (     ))

    # Symbols implied to y
    verify_assignable("Y_IMP_Y_VIS_BOOL", (0, 2), (0, 2))
    verify_assignable("Y_IMP_M_VIS_BOOL", (    ), (0, 2))  # Vis. promoted
    verify_assignable("Y_IMP_N_VIS_BOOL", (    ), (    ))
    verify_assignable("Y_IMP_Y_VIS_TRI",  (0, 2), (0, 2))  # m removed by imply
    verify_assignable("Y_IMP_M_VIS_TRI",  (    ), (0, 2))  # m promoted to y by imply
    verify_assignable("Y_IMP_N_VIS_TRI",  (    ), (    ))

    # Symbols implied to m (never affects assignable values)
    verify_assignable("M_IMP_Y_VIS_BOOL", (0, 2), (0,    2))
    verify_assignable("M_IMP_M_VIS_BOOL", (    ), (0,    2))  # Vis. promoted
    verify_assignable("M_IMP_N_VIS_BOOL", (    ), (       ))
    verify_assignable("M_IMP_Y_VIS_TRI",  (0, 2), (0, 1, 2))
    verify_assignable("M_IMP_M_VIS_TRI",  (    ), (0, 1   ))
    verify_assignable("M_IMP_N_VIS_TRI",  (    ), (       ))

    # Symbols in y-mode choice
    verify_assignable("Y_CHOICE_BOOL",           (2,), (2,))
    verify_assignable("Y_CHOICE_TRISTATE",       (2,), (2,))
    verify_assignable("Y_CHOICE_N_VIS_TRISTATE", (  ), (  ))

    # Symbols in m/y-mode choice, starting out in m mode, or y mode when
    # running without modules
    verify_assignable("MY_CHOICE_BOOL",           (2,), (    ))
    verify_assignable("MY_CHOICE_TRISTATE",       (2,), (0, 1))
    verify_assignable("MY_CHOICE_N_VIS_TRISTATE", (  ), (    ))

    c.named_choices["MY_CHOICE"].set_value(2)

    # Symbols in m/y-mode choice, now in y mode
    verify_assignable("MY_CHOICE_BOOL",           (2,), (2,))
    verify_assignable("MY_CHOICE_TRISTATE",       (2,), (2,))
    verify_assignable("MY_CHOICE_N_VIS_TRISTATE", (  ), (  ))

    def verify_choice_assignable(choice_name, assignable_no_modules,
                                 assignable_modules):
        verify_assignable_imp(c.named_choices[choice_name],
                              assignable_no_modules,
                              assignable_modules)

    # Choices with various possible modes
    verify_choice_assignable("Y_CHOICE",   (2,  ), (      2,))
    verify_choice_assignable("MY_CHOICE",  (2,  ), (   1, 2 ))
    verify_choice_assignable("NMY_CHOICE", (0, 2), (0, 1, 2 ))
    verify_choice_assignable("NY_CHOICE",  (0, 2), (0,    2 ))
    verify_choice_assignable("NM_CHOICE",  (    ), (0, 1    ))
    verify_choice_assignable("M_CHOICE",   (    ), (   1,   ))
    verify_choice_assignable("N_CHOICE",   (    ), (        ))


    print("Testing object relations")

    c = Kconfig("Kconfiglib/tests/Krelation")

    verify(c.syms["A"].nodes[0].parent is c.top_node,
           "A's parent should be the top node")

    verify(c.syms["B"].nodes[0].parent.item is c.named_choices["CHOICE_1"],
           "B's parent should be the first choice")

    verify(c.syms["C"].nodes[0].parent.item is c.syms["B"],
           "C's parent should be B (due to auto menus)")

    verify(c.syms["E"].nodes[0].parent.item == MENU,
           "E's parent should be a menu")

    verify(c.syms["E"].nodes[0].parent.parent is c.top_node,
           "E's grandparent should be the top node")

    verify(c.syms["G"].nodes[0].parent.item is c.named_choices["CHOICE_2"],
           "G's parent should be the second choice")

    verify(c.syms["G"].nodes[0].parent.parent.item == MENU,
           "G's grandparent should be a menu")


    print("Testing hex/int ranges")

    c = Kconfig("Kconfiglib/tests/Krange")

    for sym_name in "HEX_NO_RANGE", "INT_NO_RANGE", "HEX_40", "INT_40":
        sym = c.syms[sym_name]
        verify(not sym.ranges,
               "{} should not have ranges".format(sym_name))

    for sym_name in "HEX_ALL_RANGES_DISABLED", "INT_ALL_RANGES_DISABLED", \
                    "HEX_RANGE_10_20_LOW_DEFAULT", \
                    "INT_RANGE_10_20_LOW_DEFAULT":
        sym = c.syms[sym_name]
        verify(sym.ranges, "{} should have ranges".format(sym_name))

    # hex/int symbols without defaults should get no default value
    verify_value("HEX_NO_RANGE", "")
    verify_value("INT_NO_RANGE", "")
    # And neither if all ranges are disabled
    verify_value("HEX_ALL_RANGES_DISABLED", "")
    verify_value("INT_ALL_RANGES_DISABLED", "")
    # Make sure they are assignable though, and test that the form of the user
    # value is reflected in the value for hex symbols
    assign_and_verify("HEX_NO_RANGE", "0x123")
    assign_and_verify("HEX_NO_RANGE", "123")
    assign_and_verify("INT_NO_RANGE", "123")

    # Defaults outside of the valid range should be clamped
    verify_value("HEX_RANGE_10_20_LOW_DEFAULT", "0x10")
    verify_value("HEX_RANGE_10_20_HIGH_DEFAULT", "0x20")
    verify_value("INT_RANGE_10_20_LOW_DEFAULT", "10")
    verify_value("INT_RANGE_10_20_HIGH_DEFAULT", "20")
    # Defaults inside the valid range should be preserved. For hex symbols,
    # they should additionally use the same form as in the assignment.
    verify_value("HEX_RANGE_10_20_OK_DEFAULT", "0x15")
    verify_value("HEX_RANGE_10_20_OK_DEFAULT_ALTERNATE", "15")
    verify_value("INT_RANGE_10_20_OK_DEFAULT", "15")

    # hex/int symbols with no defaults but valid ranges should default to the
    # lower end of the range if it's > 0
    verify_value("HEX_RANGE_10_20", "0x10")
    verify_value("HEX_RANGE_0_10", "")
    verify_value("INT_RANGE_10_20", "10")
    verify_value("INT_RANGE_0_10", "")
    verify_value("INT_RANGE_NEG_10_10", "")

    # User values and dependent ranges

    def verify_range(sym_name, low, high, default):
        """
        Tests that the values in the range 'low'-'high' can be assigned, and
        that assigning values outside this range reverts the value back to
        'default' (None if it should revert back to "").
        """
        is_hex = (c.syms[sym_name].type == HEX)

        for i in range(low, high + 1):
            assign_and_verify_user_value(sym_name, str(i), str(i), True)
            if is_hex:
                # The form of the user value should be preserved for hex
                # symbols
                assign_and_verify_user_value(sym_name, hex(i), hex(i), True)

        # Verify that assigning a user value just outside the range causes
        # defaults to be used

        if default is None:
            default_str = ""
        else:
            default_str = hex(default) if is_hex else str(default)

        if is_hex:
            too_low_str = hex(low - 1)
            too_high_str = hex(high + 1)
        else:
            too_low_str = str(low - 1)
            too_high_str = str(high + 1)

        assign_and_verify_value(sym_name, too_low_str, default_str)
        assign_and_verify_value(sym_name, too_high_str, default_str)

    verify_range("HEX_RANGE_10_20_LOW_DEFAULT",  0x10, 0x20,  0x10)
    verify_range("HEX_RANGE_10_20_HIGH_DEFAULT", 0x10, 0x20,  0x20)
    verify_range("HEX_RANGE_10_20_OK_DEFAULT",   0x10, 0x20,  0x15)

    verify_range("INT_RANGE_10_20_LOW_DEFAULT",  10,   20,    10)
    verify_range("INT_RANGE_10_20_HIGH_DEFAULT", 10,   20,    20)
    verify_range("INT_RANGE_10_20_OK_DEFAULT",   10,   20,    15)

    verify_range("HEX_RANGE_10_20",              0x10, 0x20,  0x10)

    verify_range("INT_RANGE_10_20",              10,  20,     10)
    verify_range("INT_RANGE_0_10",               0,   10,     None)
    verify_range("INT_RANGE_NEG_10_10",          -10, 10,     None)

    # Dependent ranges

    verify_value("HEX_40", "40")
    verify_value("INT_40", "40")

    c.syms["HEX_RANGE_10_20"].unset_value()
    c.syms["INT_RANGE_10_20"].unset_value()
    verify_value("HEX_RANGE_10_40_DEPENDENT", "0x10")
    verify_value("INT_RANGE_10_40_DEPENDENT", "10")
    c.syms["HEX_RANGE_10_20"].set_value("15")
    c.syms["INT_RANGE_10_20"].set_value("15")
    verify_value("HEX_RANGE_10_40_DEPENDENT", "0x15")
    verify_value("INT_RANGE_10_40_DEPENDENT", "15")
    c.unset_values()
    verify_range("HEX_RANGE_10_40_DEPENDENT", 0x10, 0x40,  0x10)
    verify_range("INT_RANGE_10_40_DEPENDENT", 10,   40,    10)

    # Ranges and symbols defined in multiple locations

    verify_value("INACTIVE_RANGE", "2")
    verify_value("ACTIVE_RANGE", "1")


    print("Testing defconfig_filename")

    c = Kconfig("Kconfiglib/tests/empty")
    verify(c.defconfig_filename is None,
           "defconfig_filename should be None with no defconfig_list symbol")

    c = Kconfig("Kconfiglib/tests/Kdefconfig_nonexistent")
    verify(c.defconfig_filename is None,
           "defconfig_filename should be None when none of the files in the "
           "defconfig_list symbol exist")

    # Referenced in Kdefconfig_existent(_but_n)
    os.environ["BAR"] = "defconfig_2"

    c = Kconfig("Kconfiglib/tests/Kdefconfig_existent_but_n")
    verify(c.defconfig_filename is None,
           "defconfig_filename should be None when the condition is n for all "
           "the defaults")

    c = Kconfig("Kconfiglib/tests/Kdefconfig_existent")
    verify(c.defconfig_filename == "Kconfiglib/tests/defconfig_2",
           "defconfig_filename should return the existent file "
           "Kconfiglib/tests/defconfig_2")

    # Should also look relative to $srctree if the specified defconfig is a
    # relative path and can't be opened

    c = Kconfig("Kconfiglib/tests/Kdefconfig_srctree")
    verify(c.defconfig_filename == "Kconfiglib/tests/defconfig_2",
           "defconfig_filename gave wrong file with $srctree unset")

    os.environ["srctree"] = "Kconfiglib/tests"
    c = Kconfig("Kconfiglib/tests/Kdefconfig_srctree")
    verify(c.defconfig_filename == "Kconfiglib/tests/sub/defconfig_in_sub",
           "defconfig_filename gave wrong file with $srctree set")


    print("Testing mainmenu_text")

    c = Kconfig("Kconfiglib/tests/empty")
    verify(c.mainmenu_text == "Linux Kernel Configuration",
           "An empty Kconfig should get a default main menu prompt")

    # Expanded in the mainmenu text
    os.environ["FOO"] = "bar baz"
    c = Kconfig("Kconfiglib/tests/Kmainmenu")
    verify(c.mainmenu_text == "---bar baz---",
           "Wrong mainmenu text")


    print("Testing user_value")

    # References undefined env. var. Disable warnings.
    c = Kconfig("Kconfiglib/tests/Kmisc", warn=False)

    # Avoid warnings from assigning invalid user values and assigning user
    # values to symbols without prompts
    c.disable_warnings()

    syms = [c.syms[name] for name in
            ("BOOL", "TRISTATE", "STRING", "INT", "HEX")]

    for sym in syms:
        verify(sym.user_value is None,
               "{} should not have a user value to begin with")

    # Assign valid values for the types

    assign_and_verify_user_value("BOOL", 0, 0, True)
    assign_and_verify_user_value("BOOL", 2, 2, True)
    assign_and_verify_user_value("TRISTATE", 0, 0, True)
    assign_and_verify_user_value("TRISTATE", 1, 1, True)
    assign_and_verify_user_value("TRISTATE", 2, 2, True)
    assign_and_verify_user_value("STRING", "foo bar", "foo bar", True)
    assign_and_verify_user_value("INT", "123", "123", True)
    assign_and_verify_user_value("HEX", "0x123", "0x123", True)

    # Assign invalid values for the types. They should retain their old user
    # value.

    assign_and_verify_user_value("BOOL", 1, 2, False)
    assign_and_verify_user_value("BOOL", "foo", 2, False)
    assign_and_verify_user_value("BOOL", "1", 2, False)
    assign_and_verify_user_value("TRISTATE", "foo", 2, False)
    assign_and_verify_user_value("TRISTATE", "1", 2, False)
    assign_and_verify_user_value("STRING", 0, "foo bar", False)
    assign_and_verify_user_value("INT", "foo", "123", False)
    assign_and_verify_user_value("INT", 0, "123", False)
    assign_and_verify_user_value("HEX", "foo", "0x123", False)
    assign_and_verify_user_value("HEX", 0, "0x123", False)
    assign_and_verify_user_value("HEX", "-0x1", "0x123", False)

    for s in syms:
        s.unset_value()
        verify(s.user_value is None,
               "{} should not have a user value after being reset".
               format(s.name))


    print("Testing 'option env' semantics")

    os.environ["ENV_VAR"] = "ENV_VAR value"

    # References undefined env. var. and tries to assign 'option env' variable,
    # so disable warnings
    c = Kconfig("Kconfiglib/tests/Kmisc", warn=False)

    # Verify that 'option env' symbols can't be assigned user values, and that
    # 'option env' is treated like a default
    assign_and_verify_user_value("FROM_ENV", "foo", None, False)
    assign_and_verify_user_value("FROM_ENV_MISSING", "foo", None, False)
    verify_value("FROM_ENV", "ENV_VAR value")
    verify_value("FROM_ENV_MISSING", "missing")

    verify_value("FROM_ENV_WEIRD", "weird")


    print("Testing defined vs undefined symbols")

    for name in "A", "B", "C", "D", "BOOL", "TRISTATE", "STRING", "INT", "HEX":
        verify(c.syms[name].nodes,
               "{} should be defined".format(name))

    for name in "NOT_DEFINED_1", "NOT_DEFINED_2", "NOT_DEFINED_3", \
                "NOT_DEFINED_4":
        sym = c.syms[name]
        verify(not c.syms[name].nodes,
               "{} should not be defined".format(name))


    print("Testing Symbol.choice")

    for name in "A", "B", "C", "D":
        verify(c.syms[name].choice is not None,
               "{} should be a choice symbol".format(name))

    for name in "Q1", "Q2", "Q3", "BOOL", "TRISTATE", "STRING", "INT", "HEX", \
                "FROM_ENV", "FROM_ENV_MISSING", "NOT_DEFINED_1", \
                "NOT_DEFINED_2", "NOT_DEFINED_3", "NOT_DEFINED_4":
        verify(c.syms[name].choice is None,
               "{} should not be a choice symbol".format(name))


    print("Testing is_allnoconfig_y")

    verify(not c.syms["NOT_ALLNOCONFIG_Y"].is_allnoconfig_y,
           "NOT_ALLNOCONFIG_Y should not be allnoconfig_y")
    verify(c.syms["ALLNOCONFIG_Y"].is_allnoconfig_y,
           "ALLNOCONFIG_Y should be allnoconfig_y")


    print("Testing UNAME_RELEASE")

    verify_value("UNAME_RELEASE", platform.uname()[2])
    ur = c.syms["UNAME_RELEASE"]
    verify(ur.kconfig is c and
           ur.type == STRING and
           ur.env_var == "<uname release>",
           "UNAME_RELEASE has wrong fields")


    print("Testing .config reading and writing")

    config_test_file = "Kconfiglib/tests/config_test"

    def write_and_verify_header(header):
        c.write_config(config_test_file, header)
        c.load_config(config_test_file)
        verify(c.config_header == header,
               "The header {} morphed into {} on loading"
               .format(repr(header), repr(c.config_header)))

    def verify_file_contents(fname, contents):
        with open(fname, "r") as f:
            file_contents = f.read()
            verify(file_contents == contents,
                   "{} contains '{}'. Expected '{}'."
                   .format(fname, file_contents, contents))

    # Writing/reading strings with characters that need to be escaped

    c = Kconfig("Kconfiglib/tests/Kescape")

    # Test the default value
    c.write_config(config_test_file + "_from_def", header="")
    verify_file_contents(config_test_file + "_from_def",
                         r'''CONFIG_STRING="\"\\"''' "\n")
    # Write our own value
    c.syms["STRING"].set_value(r'''\"a'\\''')
    c.write_config(config_test_file + "_from_user", header="")
    verify_file_contents(config_test_file + "_from_user",
                         r'''CONFIG_STRING="\\\"a'\\\\"''' "\n")

    # Read back the two configs and verify the respective values
    c.load_config(config_test_file + "_from_def")
    verify_value("STRING", '"\\')
    c.load_config(config_test_file + "_from_user")
    verify_value("STRING", r'''\"a'\\''')

    # Appending values from a .config

    c = Kconfig("Kconfiglib/tests/Kappend")

    # Values before assigning
    verify_value("BOOL", "n")
    verify_value("STRING", "")

    # Assign BOOL
    c.load_config("Kconfiglib/tests/config_set_bool", replace=False)
    verify_value("BOOL", "y")
    verify_value("STRING", "")

    # Assign STRING
    c.load_config("Kconfiglib/tests/config_set_string", replace=False)
    verify_value("BOOL", "y")
    verify_value("STRING", "foo bar")

    # Reset BOOL
    c.load_config("Kconfiglib/tests/config_set_string")
    verify_value("BOOL", "n")
    verify_value("STRING", "foo bar")

    # Loading a completely empty .config should reset values
    c.load_config("Kconfiglib/tests/empty")
    verify_value("STRING", "")

    # An indented assignment in a .config should be ignored
    c.load_config("Kconfiglib/tests/config_indented")
    verify_value("IGNOREME", "y")


    print("Testing Kconfig fetching and separation")

    for c in Kconfig("Kconfiglib/tests/Kmisc", warn=False), \
             Kconfig("Kconfiglib/tests/Kmisc", warn=False):
        for item in c.syms["BOOL"], \
                    c.syms["BOOL"].nodes[0], \
                    c.named_choices["OPTIONAL"], \
                    c.named_choices["OPTIONAL"].nodes[0], \
                    c.syms["MENU_HOOK"].nodes[0].next, \
                    c.syms["COMMENT_HOOK"].nodes[0].next:
            verify(item.kconfig is c,
                   ".kconfig not properly set for " + repr(item))


    print("Testing imply semantics")

    c = Kconfig("Kconfiglib/tests/Kimply")

    verify_value("IMPLY_DIRECT_DEPS", "y")
    verify_value("UNMET_DIRECT_1", "n")
    verify_value("UNMET_DIRECT_2", "n")
    verify_value("UNMET_DIRECT_3", "n")
    verify_value("MET_DIRECT_1", "y")
    verify_value("MET_DIRECT_2", "y")
    verify_value("MET_DIRECT_3", "y")
    verify_value("MET_DIRECT_4", "y")

    verify_value("IMPLY_COND", "y")
    verify_value("IMPLIED_N_COND", "n")
    verify_value("IMPLIED_M_COND", "m")
    verify_value("IMPLIED_Y_COND", "y")

    verify_value("IMPLY_N_1", "n")
    verify_value("IMPLY_N_2", "n")
    verify_value("IMPLIED_FROM_N_1", "n")
    verify_value("IMPLIED_FROM_N_2", "n")

    verify_value("IMPLY_M", "m")
    verify_value("IMPLIED_M", "m")
    verify_value("IMPLIED_M_BOOL", "y")

    verify_value("IMPLY_M_TO_Y", "y")
    verify_value("IMPLIED_M_TO_Y", "y")

    # Test user value semantics

    # Verify that IMPLIED_TRISTATE is invalidated if the direct
    # dependencies change

    assign_and_verify("IMPLY", 2)
    assign_and_verify("DIRECT_DEP", 2)
    verify_value("IMPLIED_TRISTATE", 2)
    assign_and_verify("DIRECT_DEP", 0)
    verify_value("IMPLIED_TRISTATE", 0)
    # Set back for later tests
    assign_and_verify("DIRECT_DEP", 2)

    # Verify that IMPLIED_TRISTATE can be set to anything when IMPLY has value
    # n, and that it gets the value n by default (for non-imply-related
    # reasons)

    assign_and_verify("IMPLY", 0)
    assign_and_verify("IMPLIED_TRISTATE", 0)
    assign_and_verify("IMPLIED_TRISTATE", 1)
    assign_and_verify("IMPLIED_TRISTATE", 2)
    c.syms["IMPLIED_TRISTATE"].unset_value()
    verify_value("IMPLIED_TRISTATE", "n")

    # Same as above for m. Anything still goes, but m by default now.

    assign_and_verify("IMPLY", 1)
    assign_and_verify("IMPLIED_TRISTATE", 0)
    assign_and_verify("IMPLIED_TRISTATE", 1)
    assign_and_verify("IMPLIED_TRISTATE", 2)
    c.syms["IMPLIED_TRISTATE"].unset_value()
    verify_value("IMPLIED_TRISTATE", 1)

    # Same as above for y. Only n and y should be accepted. m gets promoted to
    # y. Default should be y.

    assign_and_verify("IMPLY", 2)
    assign_and_verify("IMPLIED_TRISTATE", 0)
    assign_and_verify_value("IMPLIED_TRISTATE", 1, 2)
    assign_and_verify("IMPLIED_TRISTATE", 2)
    c.syms["IMPLIED_TRISTATE"].unset_value()
    verify_value("IMPLIED_TRISTATE", 2)

    # Being implied to either m or y should give a bool the value y

    c.syms["IMPLY"].unset_value()
    verify_value("IMPLIED_BOOL", 0)
    assign_and_verify("IMPLY", 0)
    verify_value("IMPLIED_BOOL", 0)
    assign_and_verify("IMPLY", 1)
    verify_value("IMPLIED_BOOL", 2)
    assign_and_verify("IMPLY", 2)
    verify_value("IMPLIED_BOOL", 2)

    # A bool implied to m or y can take the values n and y

    c.syms["IMPLY"].set_value(1)
    assign_and_verify("IMPLIED_BOOL", 0)
    assign_and_verify("IMPLIED_BOOL", 2)

    c.syms["IMPLY"].set_value(2)
    assign_and_verify("IMPLIED_BOOL", 0)
    assign_and_verify("IMPLIED_BOOL", 2)


    print("Testing choice semantics")

    # Would warn for choice value symbols defined without a type, even
    # though the type is automatically derived. This is probably more
    # helpful than ignoring those cases, as this feature isn't used
    # deliberately anywhere from what I've seen.
    c = Kconfig("Kconfiglib/tests/Kchoice", warn=False)

    for name in "BOOL", "BOOL_OPT", "BOOL_M", "DEFAULTS":
        verify(c.named_choices[name].orig_type == BOOL,
               "choice {} should have type bool".format(name))

    for name in "TRISTATE", "TRISTATE_OPT", "TRISTATE_M":
        verify(c.named_choices[name].orig_type == TRISTATE,
               "choice {} should have type tristate".format(name))

    def select_and_verify(sym):
        choice = sym.nodes[0].parent.item
        choice.set_value(2)

        sym.set_value(2)

        verify(sym.choice.selection is sym,
               sym.name + " should be the selected symbol")

        verify(choice.user_selection is sym,
               sym.name + " should be the user selection of the choice")

        verify(sym.tri_value == 2,
               sym.name + " should have value y when selected")

        verify(sym.user_value == 2,
               sym.name + " should have user value y when selected")

        for sibling in choice.syms:
            if sibling is not sym:
                verify(sibling.tri_value == 0,
                       sibling.name + " should be n when not selected")

    def select_and_verify_all(choice_name):
        choice = c.named_choices[choice_name]

        # Select in forward order
        for sym in choice.syms:
            select_and_verify(sym)

        # Select in reverse order
        for sym in reversed(choice.syms):
            select_and_verify(sym)

    def verify_mode(choice_name, no_modules_mode, modules_mode):
        choice = c.named_choices[choice_name]

        c.modules.set_value(0)
        verify(choice.tri_value == no_modules_mode,
               'Wrong mode for choice {} with no modules. Expected {}, got {}.'
               .format(choice.name, no_modules_mode, choice.tri_value))

        c.modules.set_value(2)
        verify(choice.tri_value == modules_mode,
               'Wrong mode for choice {} with modules. Expected {}, got {}.'
               .format(choice.name, modules_mode, choice.tri_value))

    verify_mode("BOOL",         2, 2)
    verify_mode("BOOL_OPT",     0, 0)
    verify_mode("TRISTATE",     2, 1)
    verify_mode("TRISTATE_OPT", 0, 0)
    verify_mode("BOOL_M",       0, 2)
    verify_mode("TRISTATE_M",   0, 1)

    # Test defaults

    choice = c.named_choices["DEFAULTS"]

    c.syms["TRISTATE_SYM"].set_value(0)
    verify(choice.selection is c.syms["OPT_4"],
           "Wrong choice default with TRISTATE_SYM = n")

    c.syms["TRISTATE_SYM"].set_value(2)
    verify(choice.selection is c.syms["OPT_2"],
           "Wrong choice default with TRISTATE_SYM = y")

    c.syms["OPT_1"].set_value(2)
    verify(choice.selection is c.syms["OPT_1"],
           "User selection should override defaults")

    verify(c.named_choices["DEFAULTS_NOT_VISIBLE"].selection
           is c.syms["OPT_8"],
           "Non-visible choice symbols should cause the next default to be "
           "considered")

    # Test y mode selection

    c.modules.set_value(2)

    select_and_verify_all("BOOL")
    select_and_verify_all("BOOL_OPT")
    select_and_verify_all("TRISTATE")
    select_and_verify_all("TRISTATE_OPT")
    # For BOOL_M, the mode should have been promoted
    select_and_verify_all("BOOL_M")

    # Test m mode selection

    c.named_choices["TRISTATE"].set_value(1)

    verify(c.named_choices["TRISTATE"].tri_value == 1,
           "TRISTATE choice should have mode m after explicit mode assignment")

    assign_and_verify_value("T_1", 0, 0)
    assign_and_verify_value("T_2", 0, 0)
    assign_and_verify_value("T_1", 1, 1)
    assign_and_verify_value("T_2", 1, 1)
    assign_and_verify_value("T_1", 2, 1)
    assign_and_verify_value("T_2", 2, 1)

    # Switching to y mode should cause T_2 to become selected
    c.named_choices["TRISTATE"].set_value(2)
    verify_value("T_1", 0)
    verify_value("T_2", 2)

    # Verify that choices with no explicitly specified type get the type of the
    # first contained symbol with a type

    verify(c.named_choices["NO_TYPE_BOOL"].orig_type == BOOL,
           "Expected first choice without explicit type to have type bool")

    verify(c.named_choices["NO_TYPE_TRISTATE"].orig_type == TRISTATE,
           "Expected second choice without explicit type to have type "
           "tristate")

    # Verify that symbols without a type in the choice get the type of the
    # choice

    for name in "MMT_1", "MMT_2", "MMT_4", "MMT_5":
        verify(c.syms[name].orig_type == BOOL,
               "Expected {} to get type bool".format(name))

    verify(c.syms["MMT_3"].orig_type == TRISTATE,
           "Expected MMT_3 to have type tristate")

    # Verify that symbols in choices that depend on the preceding symbol aren't
    # considered choice symbols

    weird_choice = c.named_choices["WEIRD_SYMS"]

    def verify_is_normal_choice_symbol(name):
        sym = c.syms[name]
        verify(sym.choice is not None and
               sym in weird_choice.syms and
               sym.nodes[0].parent.item is weird_choice,
               "{} should be a normal choice symbol".format(sym.name))

    def verify_is_weird_choice_symbol(name):
        sym = c.syms[name]
        verify(sym.choice is None and
               sym not in weird_choice.syms,
               "{} should be a weird (non-)choice symbol"
               .format(sym.name))

    verify_is_normal_choice_symbol("WS1")
    verify_is_weird_choice_symbol("WS2")
    verify_is_weird_choice_symbol("WS3")
    verify_is_weird_choice_symbol("WS4")
    verify_is_weird_choice_symbol("WS5")
    verify_is_normal_choice_symbol("WS6")
    verify_is_weird_choice_symbol("WS7")
    verify_is_weird_choice_symbol("WS8")
    verify_is_normal_choice_symbol("WS9")


    print("\nAll selftests passed\n" if all_passed else
          "\nSome selftests failed\n")

def run_compatibility_tests():
    """
    Runs tests on configurations from the kernel. Tests compability with the
    C implementation by comparing outputs.
    """
    os.environ.pop("ARCH", None)
    os.environ.pop("SRCARCH", None)
    os.environ.pop("srctree", None)

    if speedy and not os.path.exists("scripts/kconfig/conf"):
        print("\nscripts/kconfig/conf does not exist -- running "
              "'make allnoconfig' to build it...")
        shell("make allnoconfig")

    print("Running compatibility tests...\n")

    # The set of tests that want to run for all architectures in the kernel
    # tree -- currently, all tests. The boolean flag indicates whether .config
    # (generated by the C implementation) should be compared to ._config
    # (generated by us) after each invocation.
    all_arch_tests = ((test_load,           False),
                      (test_alldefconfig,   True),
                      # Needs to report success/failure for each arch/defconfig
                      # combo, hence False.
                      (test_defconfig,      False),
                      (test_sanity,         False),
                      (test_all_no,         True),
                      (test_all_no_simpler, True),
                      (test_all_yes,        True))

    for test_fn, compare_configs in all_arch_tests:
        # The test description is taken from the docstring of the corresponding
        # function
        print(textwrap.dedent(test_fn.__doc__))

        # Previously we used to load all the arches once and keep them
        # around for the tests. That now uses a huge amount of memory (pypy
        # helps a bit), so reload them for each test instead.
        for kconf, arch, srcarch in all_arch_srcarch_kconfigs():
            rm_configs()
            test_fn(kconf, arch, srcarch)

            if compare_configs:
                if equal_confs():
                    print("{:14}OK".format(arch))
                else:
                    print("{:14}FAIL".format(arch))
                    fail()

    if all_passed:
        print("All selftests and compatibility tests passed")
        print("{} arch/defconfig pairs tested".format(nconfigs))
    else:
        print("Some tests failed")
        sys.exit(1)

def all_arch_srcarch_pairs():
    for srcarch in os.listdir("arch"):
        if os.path.exists(os.path.join("arch", srcarch, "Kconfig")):
            yield (srcarch, srcarch)

    # Some arches define additional ARCH settings with ARCH != SRCARCH
    # (search for "Additional ARCH settings for" in the top-level Makefile)

    yield ("i386", "x86")
    yield ("x86_64", "x86")

    yield ("sparc32", "sparc")
    yield ("sparc64", "sparc")

    yield ("sh64", "sh")

    yield ("tilepro", "tile")
    yield ("tilegx", "tile")

def all_arch_srcarch_kconfigs():
    for arch, srcarch in all_arch_srcarch_pairs():
        os.environ["ARCH"] = arch
        os.environ["SRCARCH"] = srcarch
        yield (Kconfig(), arch, srcarch)

def test_load(conf, arch, srcarch):
   """
   Load all arch Kconfigs to make sure we don't throw any errors
   """
   print("{:14}OK".format(arch))

def test_all_no(conf, arch, srcarch):
    """
    Verify that our examples/allnoconfig.py script generates the same .config
    as 'make allnoconfig', for each architecture. Runs the script via
    'make scriptconfig', so kinda slow even in speedy mode.
    """
    # TODO: Support speedy mode for running the script
    shell("make scriptconfig SCRIPT=Kconfiglib/examples/allnoconfig.py "
          "PYTHONCMD='{}'".format(sys.executable))
    shell("mv .config ._config")
    if speedy:
        shell("scripts/kconfig/conf --allnoconfig Kconfig")
    else:
        shell("make allnoconfig")

def test_all_no_simpler(conf, arch, srcarch):
    """
    Verify that our examples/allnoconfig_simpler.py script generates the same
    .config as 'make allnoconfig', for each architecture. Runs the script via
    'make scriptconfig', so kinda slow even in speedy mode.
    """
    # TODO: Support speedy mode for running the script
    shell("make scriptconfig SCRIPT=Kconfiglib/examples/allnoconfig_simpler.py "
          "PYTHONCMD='{}'".format(sys.executable))
    shell("mv .config ._config")
    if speedy:
        shell("scripts/kconfig/conf --allnoconfig Kconfig")
    else:
        shell("make allnoconfig")

def test_all_yes(conf, arch, srcarch):
    """
    Verify that our examples/allyesconfig.py script generates the same .config
    as 'make allyesconfig', for each architecture. Runs the script via
    'make scriptconfig', so kinda slow even in speedy mode.
    """
    # TODO: Support speedy mode for running the script
    shell("make scriptconfig SCRIPT=Kconfiglib/examples/allyesconfig.py "
          "PYTHONCMD='{}'".format(sys.executable))
    shell("mv .config ._config")
    if speedy:
        shell("scripts/kconfig/conf --allyesconfig Kconfig")
    else:
        shell("make allyesconfig")

def test_sanity(conf, arch, srcarch):
    """
    Do sanity checks on each configuration and call all public methods on all
    symbols, choices, and menu nodes for all architectures to make sure we
    never crash or hang.
    """
    print("For {}...".format(arch))

    conf.modules
    conf.defconfig_list
    conf.defconfig_filename
    conf.enable_redun_warnings()
    conf.disable_redun_warnings()
    conf.enable_undef_warnings()
    conf.disable_undef_warnings()
    conf.enable_warnings()
    conf.disable_warnings()
    conf.mainmenu_text
    conf.unset_values()

    # Python 2/3 compatible
    for key, sym in conf.syms.items():
        verify(isinstance(key, str), "weird key '{}' in syms dict".format(key))

        if sym.name != "UNAME_RELEASE":
            verify(not sym.is_constant, sym.name + " in 'syms' and constant")

        verify(sym not in conf.const_syms,
               sym.name + " in both 'syms' and 'const_syms'")

        for dep in sym._dependents:
            verify(not dep.is_constant,
                   "the constant symbol {} depends on {}"
                   .format(dep.name, sym.name))

        sym.__repr__()
        sym.__str__()
        sym.assignable
        conf.disable_warnings()
        sym.set_value(2)
        sym.set_value("foo")
        sym.unset_value()
        conf.enable_warnings()
        sym.str_value
        sym.tri_value
        sym.type
        sym.user_value
        sym.visibility

    for sym in conf.defined_syms:
       verify(sym.nodes, sym.name + " is defined but lacks menu nodes")

       verify(not (sym.orig_type not in (BOOL, TRISTATE) and sym.choice),
              sym.name + " is a choice symbol but not bool/tristate")

    for key, sym in conf.const_syms.items():
        verify(isinstance(key, str),
               "weird key '{}' in const_syms dict".format(key))

        verify(sym.is_constant,
               '"{}" is in const_syms but not marked constant'
               .format(sym.name))

        verify(not sym.nodes,
               '"{}" is constant but has menu nodes'.format(sym.name))

        verify(not sym._dependents,
               '"{}" is constant but is a dependency of some symbol'
               .format(sym.name))

        verify(not sym.choice,
               '"{}" is constant and a choice symbol'.format(sym.name))

        sym.__repr__()
        sym.__str__()
        sym.assignable
        conf.disable_warnings()
        sym.set_value(2)
        sym.set_value("foo")
        sym.unset_value()
        conf.enable_warnings()
        sym.str_value
        sym.tri_value
        sym.type
        sym.visibility

    # Cheat with internals
    for choice in conf._choices:
        for sym in choice.syms:
            verify(sym.choice is choice,
                   "{0} is in choice.syms but 'sym.choice' is not the choice"
                   .format(sym.name))

            verify(sym.type in (BOOL, TRISTATE),
                   "{} is a choice symbol but is not a bool/tristate"
                   .format(sym.name))

        choice.__str__()
        choice.__repr__()
        choice.str_value
        choice.tri_value
        choice.user_value
        choice.assignable
        choice.selection
        choice.type
        choice.visibility

    # Menu nodes

    node = conf.top_node

    while 1:
        # Everything else should be well exercised elsewhere
        node.__repr__()
        node.__str__()
        verify(isinstance(node.item, (Symbol, Choice)) or \
               node.item in (MENU, COMMENT),
               "'{}' appeared as a menu item".format(node.item))

        if node.list is not None:
            node = node.list

        elif node.next is not None:
            node = node.next

        else:
            while node.parent is not None:
                node = node.parent
                if node.next is not None:
                    node = node.next
                    break
            else:
                break

def test_alldefconfig(conf, arch, srcarch):
    """
    Verify that Kconfiglib generates the same .config as 'make alldefconfig',
    for each architecture
    """
    conf.write_config("._config")
    if speedy:
        shell("scripts/kconfig/conf --alldefconfig Kconfig")
    else:
        shell("make alldefconfig")

def test_defconfig(conf, arch, srcarch):
    """
    Verify that Kconfiglib generates the same .config as scripts/kconfig/conf,
    for each architecture/defconfig pair. In obsessive mode, this test includes
    nonsensical groupings of arches with defconfigs from other arches (every
    arch/defconfig combination) and takes an order of magnitude longer time to
    run.

    With logging enabled, this test appends any failures to a file
    test_defconfig_fails in the root.
    """
    global nconfigs
    defconfigs = []

    def add_configs_for_arch(srcarch):
        srcarch_dir = os.path.join("arch", srcarch)

        # Some arches have a "defconfig" in the root of their arch/<arch>/
        # directory
        root_defconfig = os.path.join(srcarch_dir, "defconfig")
        if os.path.exists(root_defconfig):
            defconfigs.append(root_defconfig)

        # Assume all files in the arch/<arch>/configs directory (if it
        # exists) are configurations
        defconfigs_dir = os.path.join(srcarch_dir, "configs")
        if not os.path.exists(defconfigs_dir):
            return

        if not os.path.isdir(defconfigs_dir):
            print("Warning: '{}' is not a directory - skipping"
                  .format(defconfigs_dir))
            return

        for dirpath, _, filenames in os.walk(defconfigs_dir):
            for filename in filenames:
                defconfigs.append(os.path.join(dirpath, filename))

    if obsessive:
        # Collect all defconfigs. This could be done once instead, but it's
        # a speedy operation comparatively.
        for arch_ in os.listdir("arch"):
            add_configs_for_arch(arch_)
    else:
        add_configs_for_arch(srcarch)

    # Test architecture for each defconfig

    for defconfig in defconfigs:
        rm_configs()

        nconfigs += 1

        conf.load_config(defconfig)
        conf.write_config("._config")
        if speedy:
            shell("scripts/kconfig/conf --defconfig='{}' Kconfig".
                  format(defconfig))
        else:
            shell("cp {} .config".format(defconfig))
            # It would be a bit neater if we could use 'make *_defconfig'
            # here (for example, 'make i386_defconfig' loads
            # arch/x86/configs/i386_defconfig' if ARCH = x86/i386/x86_64),
            # but that wouldn't let us test nonsensical combinations of
            # arches and defconfigs, which is a nice way to find obscure
            # bugs.
            shell("make kconfiglibtestconfig")

        arch_defconfig_str = "  {:14}with {:60} ".format(arch, defconfig)

        if equal_confs():
            print(arch_defconfig_str + "OK")
        else:
            print(arch_defconfig_str + "FAIL")
            fail()
            if log:
                with open("test_defconfig_fails", "a") as fail_log:
                    fail_log.write("{}  {} with {} did not match\n"
                            .format(time.strftime("%d %b %Y %H:%M:%S",
                                                  time.localtime()),
                                    arch, defconfig))

#
# Helper functions
#

def rm_configs():
    """
    Delete any old ".config" (generated by the C implementation) and
    "._config" (generated by us), if present.
    """
    def rm_if_exists(f):
        if os.path.exists(f):
            os.remove(f)

    rm_if_exists(".config")
    rm_if_exists("._config")

def equal_confs():
    with open(".config") as f:
        their = f.readlines()

    # Strip the header generated by 'conf'
    i = 0
    for line in their:
        if not line.startswith("#") or \
           re.match(r"# CONFIG_(\w+) is not set", line):
            break
        i += 1
    their = their[i:]

    try:
        f = open("._config")
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise
        print("._config not found. Did you forget to apply the Makefile patch?")
        return False
    else:
        with f:
            # [1:] strips the default header
            our = f.readlines()[1:]

    if their == our:
        return True

    # Print a unified diff to help debugging
    print("Mismatched .config's! Unified diff:")
    sys.stdout.writelines(difflib.unified_diff(their, our, fromfile="their",
                                               tofile="our"))

    return False

if __name__ == "__main__":
    run_tests()

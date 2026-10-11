---
name: write-tests
description: Write or improve automated tests for existing code: unit, integration or regression tests. Use when asked to add tests, raise coverage, or lock in a bug fix.
---
# Write tests

## 1. Find the test setup
- Identify the framework, where tests live, naming, fixtures and how to run them (package scripts, pyproject, Makefile). Run the existing suite once for a baseline and note failures that were already there.

## 2. Understand the code under test
- Read it and its callers: public behaviour, inputs, outputs, side effects, error paths. Copy the style of neighbouring tests.

## 3. Choose the cases
- The normal path; boundaries (empty, one, many, the maximum); invalid input and error paths; each branch that behaves differently; known bugs as regression cases.
- Test behaviour, not implementation details. Do not mock everything; mock only what is slow, random or external.

## 4. Write clear tests
- Each name states the behaviour it checks. Arrange, act, assert. Keep tests independent and deterministic: fake the network, the clock and randomness. Keep fixtures small.

## 5. Run them
- New tests should pass on correct code. For a bug-fix regression test, confirm it fails without the fix (stash or temporarily revert the fix) and passes with it.

## 6. Check they would catch a break
- Introduce a small deliberate bug, see that a test fails, then restore the code. Never leave the sabotage behind.

## 7. Report
- List the tests added and what each covers, the command and its result, and the gaps that remain. If the tests exposed a bug, report it. Do not change the code under test to make a test pass unless asked.

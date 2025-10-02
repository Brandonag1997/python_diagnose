# Python Diagnose
## Introduction
Run a Python script, print its output, and (optionally) diagnose failures with
an LLM.
This CLI launches a target Python program, captures `stdout`/`stderr`, applies a
few fast local heuristics to suggest likely fixes, and asks an LLM
 to analyze the error and propose minimal changes.
 
---
## Requirements
- Python 3.9+
- Optional (for LLM diagnosis):
- `langchain-openai`, `dotenv`
- An OpenAI‑compatible API key available to LangChain (e.g., `OPENAI_API_KEY`)
---
## Installation
```bash
# From your project root
pip install -r requirements.txt
# or install the minimal set directly
pip install dotenv langchain-openai
```
---
## Configuration
Create a `.env` (optional) alongside `diagnose.py` or in your working directory:
```
OPENAI_API_KEY=...
```

You can also rely on the environment variable `OPENAI_API_KEY` exported in your shell.

---

## Usage

```bash
python diagnose.py path/to/script.py script_arg1 script_arg2 ...
```

### CLI options
```text
usage: diagnose.py [-h] [--timeout TIMEOUT] [--llm] [--model MODEL] [--trim TRIM] script [script_args ...]

Run a Python script and diagnose errors

positional arguments:
  script                Path to Python script
  script_args           Remaining args passed through to the script

options:
  -h, --help         show this help message and exit
  --timeout TIMEOUT  Time in seconds to analyze before timing out
  --llm, --no-llm    Use LLM (default: True)
  --model MODEL      LLM model name
  --trim TRIM        Max characters to send to LLM
```

### Examples
```bash
python diagnose.py --llm examples/division_by_zero.py 
=== stdout ===
About to divide by zero...

=== stderr ===
Traceback (most recent call last):
  File "examples/division_by_zero.py", line 7, in <module>
    print(divide(1, 0))
  File "examples/division_by_zero.py", line 3, in divide
    return a / b
ZeroDivisionError: division by zero

=== exit code ===
1
=== quick hint ===
ArithmeticError: division by zero
=== llm diagnosis ===
Root cause:
- The function divides a by b without validating b. When b is 0, Python raises ZeroDivisionError: division by zero. The script then crashes at divide(1, 0).

Minimal fixes (choose one approach):

1) Validate inputs (preferful for clear errors)
- Change divide to reject zero divisors with a clear exception.
Code:
def divide(a, b):
    if b == 0:
        raise ValueError("b must be non-zero")
    return a / b
...
```

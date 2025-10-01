"""Run a Python script, print its output, and (optionally) diagnose failures with an LLM.

This utility executes a target Python script with arguments, captures stdout/stderr,
applies a few quick local heuristics to suggest likely fixes, and asks a
language model for a diagnosi of the errors.

Typical usage:
    python diagnose.py path/to/script.py -- arg1 arg2
"""

import argparse
import re
import subprocess
import sys

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from openai import OpenAIError


def run_script(python_executable: str, script: str, script_args, timeout: int):
    """Run a Python script in a subprocess and capture its outputs.

    Parameters
    ----------
    python_executable : str
        Path to the Python interpreter to use (e.g., sys.executable).
    script : str
        Path to the Python script to run.
    script_args : Iterable[str]
        Arguments to pass to the script (everything after the script path).
    timeout : int
        Maximum number of seconds to allow the process to run before timing out.

    Returns
    -------
    tuple[str, str, int]
        A tuple of (stdout, stderr, returncode). If a timeout occurs,
        stdout may be partial (or empty), stderr will contain a timeout message,
        and the return code will be 124.

    Notes
    -----
    This function does not raise on non-zero exit codes; it returns them so
    the caller can decide how to handle failures.
    """
    cmd = [python_executable, script] + list(script_args)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as err:
        return err.stdout or "", f"TimeoutExpired afetr {timeout}s", 124


def local_hints(stderr: str) -> str:
    """Derive a quick human-readable hint from common Python error patterns.

    Parameters
    ----------
    stderr : str
        The standard error text (e.g., a traceback) from a failed script run.

    Returns
    -------
    str
        A short hint string if a known pattern is recognized; otherwise an empty string.

    Examples
    --------
    - `ZeroDivisionError:` -> `"ArithmeticError: division by zero"`
    - `ModuleNotFoundError: No module named 'foo'` -> `"ImportError: Missing dependency: pip install foo"`
    - `NameError: name 'bar' is not defined` -> `Name "bar" is undefined: typo, missing import, or assignment?`
    """
    if not stderr:
        return ""
    patterns = [
        (r"ZeroDivisionError:", lambda m: "ArithmeticError: division by zero"),
        (
            r"ModuleNotFoundError: No module named '([^']+)'",
            lambda m: f"ImportError: Missing dependency: pip install {m.group(1)}",
        ),
        (
            r"ImportError: cannot import name '([^']+)'",
            lambda m: f"Import mismatch: check the library version and import path for '{m.group(1)}'",
        ),
        (
            r"IndexError",
            lambda m: "Index Error: the value requested is outside the range",
        ),
        (
            r"NameError: name '([^']+)' is not defined",
            lambda m: f"Name '{m.group(1)}' is undefined: typo, missing import, or assignment?",
        ),
        (
            r"FileNotFoundError: \\[Errno 2\\] No such file or directory: '([^']+)'",
            lambda m: f"File not found: {m.group(1)} (check working directory/path)",
        ),
        (
            r"PermissionError: \\[Errno 13\\]",
            lambda m: "Permission denied: adjust file permissions or run with appropriate privileges",
        ),
        (
            r"SyntaxError:",
            lambda m: "Syntax error: check missing colons, parentheses, or stray characters",
        ),
        (
            r"IndentationError:",
            lambda m: "Indentation error: avoid mixing tabs/spaces; use 4 spaces",
        ),
        (
            r"TypeError:",
            lambda m: "Type error: an argument has the wrong type, check the function signature",
        ),
        (
            r"ValueError:",
            lambda m: "Value error: a value is invalid for the expected type/range",
        ),
    ]
    for pattern, l_func in patterns:
        m = re.search(pattern, stderr)
        if m:
            return l_func(m)


def get_excerpt(path: str, max_chars: int = 2000) -> str:
    """Read a file and return a head+tail excerpt, preserving context within a size budget.

    Parameters
    ----------
    path : str
        Path to the text file to read (typically a Python source file).
    max_chars : int, optional
        Target maximum character budget for the returned text, by default 2000.

    Returns
    -------
    str
        The full file content if it fits within `max_chars`. Otherwise a concatenation
        of the first half and last half separated by a trim marker. Returns an empty
        string if the file does not exist.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
            if len(text) <= max_chars:
                return text
            head = text[: max_chars // 2]
            tail = text[-max_chars // 2 :]
            return head + "\n...\n[TRIMMED]\n...\n" + tail
    except FileNotFoundError:
        return ""


def llm_diagnose(
    stderr: str, stdout: str, code_snippet: str, model: str = "gpt-5-nano"
) -> str:
    """Ask an LLM to analyze the failure and propose minimal fixes.

    The prompt includes the stderr (traceback), stdout, and a code excerpt. The
    model is expected to return a concise root cause explanation, minimal steps
    to fix, and a brief justification.

    Parameters
    ----------
    stderr : str
        Standard error output from the failed script (usually a traceback).
    stdout : str
        Standard output from the script (may help with context).
    code_snippet : str
        A snippet or excerpt of the script’s source code (may be trimmed).
    model : str, optional
        Model name for the LangChain `ChatOpenAI` wrapper, by default "gpt-5-nano".

    Returns
    -------
    str
        A diagnosis string from the model. If the LLM client cannot be
        initialized, returns a message in the form "(LLM unavaiable: <error>)".

    """
    try:
        llm = ChatOpenAI(model=model)
    except OpenAIError as err:
        return "(LLM unavaiable: " + str(err) + ")"

    system = """
        You are a careful Python error analyzer,
        Explain likely root cause, list minimal fix steps, and "why this works" concisely,
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            (
                "human",
                """
Analyze this failure and propose minimal fixes.

stderr (traceback):
{stderr}

stdout:
{stdout}

code excerpt (may be trimmed):
{code}
                """,
            ),
        ]
    )

    chain = prompt | llm | StrOutputParser()
    text = chain.invoke({"stderr": stderr, "stdout": stdout, "code": code_snippet})
    return text.strip()


def build_parser():
    """Build the argument parser for the cli.

    Returns
    -------
    argparse.ArgumentParser
        A parser with arguments:
        - script: path to the Python script to run
        - script_args: remaining args passed through to the script
        - --timeout: max seconds before timing out (default: 60)
        - --llm: if set to True, enable LLM diagnosis (default: True)
        - --model: LLM model name (default: "gpt-5-nano")
        - --trim: maximum characters sent to LLM (default: 2000), set to 0 to remove limit
    """
    p = argparse.ArgumentParser(description="Run a Python script and diagnose errors")
    p.add_argument("script", help="Path to Python script")
    p.add_argument("script_args", nargs=argparse.REMAINDER, help="Script arguments")
    p.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Time in seconds to analyze before timing out",
    )
    p.add_argument("--llm", action="store_true", help="Use LLM", default=True)
    p.add_argument("--model", default="gpt-5-nano", help="LLM model name")
    p.add_argument(
        "--trim",
        type=int,
        default=2000,
        help="Max characters to send to LLM",
    )
    return p


def main():
    """Entrypoint: run the target script, print outputs, and optionally diagnose failures.

    Workflow
    --------
    1. Load environment variables (for API keys, etc.).
    2. Parse CLI args and run the target script with a timeout.
    3. Print stdout, stderr, and exit code.
    4. If a common exception is detected, print it.
    5. If the script failed and LLM diagnosis is enabled, call the LLM and print its analysis.
    6. Exit with the target script’s return code.

    Side Effects
    ------------
    Prints to stdout and exits the process with the child return code.
    """
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    stdout, stderr, exit_code = run_script(
        sys.executable, args.script, args.script_args, args.timeout
    )

    hint = local_hints(stderr)
    print("=== stdout ===\n" + (stdout or ""))
    print("=== stderr ===\n" + (stderr or ""))
    print(f"=== exit code ===\n{exit_code}")
    if hint:
        print("=== quick hint ===\n" + hint)
    if exit_code != 0 and args.llm:
        diag = llm_diagnose(
            stderr, stdout, get_excerpt(args.script, args.trim), model=args.model
        )
        if diag:
            print("=== llm diagnosis ===\n" + diag)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

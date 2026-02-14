#!/usr/bin/env python3
import subprocess
import sys
import signal
import os
import shutil
import argparse

process = None

LAKEFILE_BASE = """\
import Lake
open Lake DSL

package «lean-project» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

{mathlib_require}
@[default_target]
lean_lib «LeanProject» where
  srcDir := "."
  globs := #[.submodules `src]
"""

MATHLIB_REQUIRE = """\
  moreLinkArgs := #["-L./.lake/packages/mathlib/.lake/build/lib", "-lMathlib"]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "master"
"""


def cleanup(signum=None, frame=None):
    print("\nShutting down server...")
    if process:
        process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def run_cmd(cmd, cwd=None, desc=None):
    """Run a command, print description, and abort on failure."""
    if desc:
        print(f"=> {desc}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"ERROR: command failed (exit {result.returncode}): {' '.join(cmd)}")
        sys.exit(1)


def write_lakefile(lean_project_dir, with_mathlib):
    """Write lakefile.lean with or without mathlib dependency."""
    content = LAKEFILE_BASE.format(
        mathlib_require=MATHLIB_REQUIRE if with_mathlib else "",
    )
    lakefile = os.path.join(lean_project_dir, "lakefile.lean")
    with open(lakefile, "w") as f:
        f.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Lean 4 collaborative editor")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--skip-setup", action="store_true", help="Skip dependency installation and lake build")
    parser.add_argument("--with-mathlib", action="store_true", help="Include mathlib dependency (slow first run)")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    lean_project_dir = os.path.join(base_dir, "lean-project")

    if not args.skip_setup:
        # ---------------------------------------------------------------
        # 1. Install Python dependencies
        # ---------------------------------------------------------------
        run_cmd(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            desc="Installing Python dependencies (pip install -r requirements.txt)",
        )

        # ---------------------------------------------------------------
        # 2. Check that elan / lake is available
        # ---------------------------------------------------------------
        if not shutil.which("lake"):
            print("ERROR: 'lake' not found in PATH.")
            print("Install Lean 4 / elan first: https://leanprover-community.github.io/get_started.html")
            sys.exit(1)

        # ---------------------------------------------------------------
        # 3. Write lakefile.lean (with or without mathlib)
        # ---------------------------------------------------------------
        write_lakefile(lean_project_dir, args.with_mathlib)
        if args.with_mathlib:
            print("=> Lakefile configured WITH mathlib", flush=True)
        else:
            print("=> Lakefile configured without mathlib (use --with-mathlib to include it)", flush=True)

        # ---------------------------------------------------------------
        # 4. Resolve Lean dependencies (lake update)
        # ---------------------------------------------------------------
        run_cmd(
            ["lake", "update"],
            cwd=lean_project_dir,
            desc="Resolving Lean dependencies (lake update)...",
        )

        # ---------------------------------------------------------------
        # 5. Fetch mathlib cache (only when mathlib is included)
        # ---------------------------------------------------------------
        if args.with_mathlib:
            run_cmd(
                ["lake", "exe", "cache", "get"],
                cwd=lean_project_dir,
                desc="Fetching mathlib cache (lake exe cache get)...",
            )

        # ---------------------------------------------------------------
        # 6. Build Lean project (errors are OK — user code may have issues)
        # ---------------------------------------------------------------
        print("=> Building Lean project (lake build)...", flush=True)
        result = subprocess.run(["lake", "build"], cwd=lean_project_dir)
        if result.returncode != 0:
            print("   (build had errors — this is OK if Scratch.lean has issues, continuing...)", flush=True)
    else:
        # Even with --skip-setup, lake must be available
        if not shutil.which("lake"):
            print("ERROR: 'lake' not found in PATH.")
            print("Install Lean 4 / elan first: https://leanprover-community.github.io/get_started.html")
            sys.exit(1)

    # -------------------------------------------------------------------
    # Start the server
    # -------------------------------------------------------------------
    cmd = [sys.executable, "server.py", "--port", str(args.port)]
    process = subprocess.Popen(cmd)

    print(f"\nLean 4 Collaborative Editor: http://localhost:{args.port}")
    print("Press Ctrl+C to stop\n")

    try:
        process.wait()
    except KeyboardInterrupt:
        cleanup()

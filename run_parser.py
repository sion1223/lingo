import os
import sys
import importlib.util

# Get absolute path to parse_kanji.py in the same directory
script_dir = os.path.dirname(os.path.abspath(__file__))
target_script = os.path.join(script_dir, "parse_kanji.py")

# Change working directory so parse_kanji finds "kanji" folder
os.chdir(script_dir)

# Import and run
spec = importlib.util.spec_from_file_location("parse_kanji", target_script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.main()

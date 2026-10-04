"""The main entry point for the Tauri app."""

import sys
from multiprocessing import freeze_support

from golang_python_translator import main


freeze_support()

sys.exit(main())

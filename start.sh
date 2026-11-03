#!/usr/bin/env python3
# Fallback only — prefer CMD ["python","-u","agent.py"]
import runpy
runpy.run_path("/app/agent.py", run_name="__main__")

"""Generate the deposit-address master secret.

Run from the project venv:

    .venv\\Scripts\\python.exe scripts/generate_deposit_master.py
"""

from casino.tools.generate_deposit_master import main

if __name__ == "__main__":
    main()

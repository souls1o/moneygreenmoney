"""Generate a Solana keypair for the house deposit wallet.

Run from the project venv:

    .venv\\Scripts\\python.exe scripts/generate_house_wallet.py
"""

from casino.tools.generate_house_wallet import main

if __name__ == "__main__":
    main()

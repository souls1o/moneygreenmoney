"""Split the old super wallet into the house wallet and player deposit addresses.

Dry run (default):

    .venv\\Scripts\\python.exe -m casino.tools.migrate_super_wallet

Send:

    .venv\\Scripts\\python.exe -m casino.tools.migrate_super_wallet --apply
"""

from casino.tools.migrate_super_wallet import main

if __name__ == "__main__":
    main()

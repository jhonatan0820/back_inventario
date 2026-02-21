import sys
import traceback

try:
    from app import app
    print("=== APP IMPORTADA CORRECTAMENTE ===")
except Exception as e:
    print("=== ERROR IMPORTANDO APP ===")
    print(traceback.format_exc())
    sys.exit(1)

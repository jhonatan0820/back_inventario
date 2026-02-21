import sys
import traceback

try:
    from app import app
    print("=== APP IMPORTADA CORRECTAMENTE ===")
except Exception as e:
    print("=== ERROR IMPORTANDO APP ===")
    print(traceback.format_exc())
    sys.exit(1)
```

Y cambia el Start Command en Render a:
```
gunicorn wsgi:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

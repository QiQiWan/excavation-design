cd /d "E:\OneDrive\python\research\foundation_design\services\api"
set "PITGUARD_DB_PATH=E:\OneDrive\python\research\foundation_design\runtime\pitguard.sqlite3"
set "PYTHONPATH=E:\OneDrive\python\research\foundation_design\services\api;%PYTHONPATH%"
"C:\Users\EatRice\.conda\envs\ifc\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 1>>"E:\OneDrive\python\research\foundation_design\runtime\backend.log" 2>>&1

modules = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "pydantic": "pydantic",
    "multipart": "python-multipart",
    "numpy": "numpy",
    "shapely": "shapely",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "meshio": "meshio",
}
missing = []
for import_name, package_name in modules.items():
    try:
        __import__(import_name)
    except Exception:
        missing.append(package_name)
print(" ".join(missing))

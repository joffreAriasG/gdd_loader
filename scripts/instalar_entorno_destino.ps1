# ============================================================================
# Instalación de gdd_loader en el servidor destino
# Correr en Anaconda Prompt (o PowerShell con "conda init powershell" ya
# hecho), logueado en el servidor con tu propio usuario de AD.
# ============================================================================

# 1) Entorno dedicado (no usar base), Python 3.11 -- coincide con lo que ya
#    hay instalado en el servidor.
conda create -n gdd_loader python=3.11 -y
conda activate gdd_loader

# 2) Copia manual de la carpeta del proyecto desde tu equipo a este servidor
#    (no hay git, así que es copia directa). Trae SOLO esto -- es justo lo
#    que tu propio .gitignore ya marca como "lo que importa" vs. lo generado:
#      src/
#      tests/
#      pyproject.toml
#      README.md
#      .env.example
#    NO copies (se regeneran o se rehacen en destino): .venv/, __pycache__/,
#    *.egg-info/, .pytest_cache/, logs/ (contenido), y sobre todo .env real
#    (tiene la ruta de OneDrive y el servidor de TU equipo, no los del
#    servidor -- lo rearmamos aparte en el paso 5).
#
# Ajusta la ruta destino a como organices el servidor:
cd C:\gdd_loader

# 3) Instala el paquete + sus dependencias declaradas en pyproject.toml
#    (pandas, openpyxl, SQLAlchemy, pyodbc, python-dotenv) + pytest (dev)
pip install -e ".[dev]"

# 4) ODBC Driver 17 for SQL Server -- pyodbc lo necesita y Anaconda no lo trae.
#    Verifica si ya está instalado en el servidor:
Get-OdbcDriver | Where-Object {$_.Name -like "*SQL Server*"}
#    Si no aparece "ODBC Driver 17 for SQL Server", descárgalo e instálalo:
#    https://go.microsoft.com/fwlink/?linkid=2249006 (x64)

# 5) Configuración -- copia la plantilla y edítala con los valores del
#    servidor (NO los de tu equipo local):
copy .env.example .env
notepad .env
#    - GDD_CARPETA_ORIGEN: ruta de la carpeta de SharePoint YA sincronizada
#      en este servidor con tu usuario (ver nota de OneDrive pendiente).
#    - GDD_SQL_SERVIDOR: nombre/instancia real del servidor SQL destino.
#    - GDD_SQL_AUTH_MODO=WINDOWS (ya viene así por defecto -- correcto,
#      es justo lo que confirmamos: autenticación Windows/AD, sin usuario
#      ni password de SQL).
#    - GDD_ESTRATEGIA_STAGING=DOMINIO (default -- no tocar salvo reset manual).

# 6) Validación antes de la primera carga real:
pytest
#    Debería dar el mismo resultado (o mejor) que en tu equipo -- si algo
#    falla aquí, es entorno/dependencias, no lógica de negocio (los tests
#    no tocan SQL Server real, así que un fallo no es por el servidor).

# 7) Prueba controlada con un archivo real conocido antes de programar la
#    tarea automática (Tarea 2.7):
python -m gdd_loader.cli --archivo AdministracionDeSeguros.xlsx

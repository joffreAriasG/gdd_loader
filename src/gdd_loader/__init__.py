"""gdd_loader: carga de plantillas de gobierno de datos hacia DGODELIVERY."""

# Fuente unica de la version (pyproject.toml la lee de aqui). Se registra en
# gdd.carga_control.version_loader y se compara contra
# gdd.plantilla_version.version_loader_minima: al hacer `git pull` el valor
# se actualiza sin necesidad de reinstalar el paquete.
__version__ = "0.4.1"

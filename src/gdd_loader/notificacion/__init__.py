"""
Notificacion de cargas (v0.5.0).

Al terminar cada archivo, procesar_cli deja un evento JSON en la carpeta
GDD_CARPETA_NOTIFICACIONES (sincronizada con SharePoint). Un flujo de Power
Automate (solo conectores estandar) lo toma y envia el correo.

- instantanea.py : foto LEGIBLE (catalogos resueltos a texto) de lo activo de
                   un dominio en gdd.*, por clave natural. Se toma antes y
                   despues del merge.
- diferencias.py : compara las dos fotos -> nuevos / modificados / dados de
                   baja, con valor anterior y nuevo por campo. Funcion pura.
- minuta.py      : arma la minuta HTML (cuerpo del correo, tipo acta para
                   aprobacion).
- evento.py      : arma el JSON (destinatarios = responsables del dominio con
                   el rol configurado, id_rol=1) y lo escribe de forma atomica.

La notificacion NUNCA afecta la carga: cualquier falla aqui se registra como
advertencia y la carga sigue su curso.
"""

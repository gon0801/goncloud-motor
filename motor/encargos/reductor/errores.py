"""Señales internas para la aplicación histórica de un evento."""


class HechoInaplicable(Exception):
    """Una carga guardada no se puede aplicar sin inventar un estado."""

#!/bin/bash

# 1. Crear directorios limpios
rm -rf icegrid/db
mkdir -p icegrid/db/registry icegrid/db/node1 icegrid/db/node2

# 2. Arrancar el Registry
echo "Arrancando IceGrid Registry..."
icegridregistry --Ice.Config=registry.config &
sleep 2 # Esperar a que arranque

# 3. Arrancar los Nodos
echo "Arrancando Nodo 1..."
icegridnode --Ice.Config=node1.config &

echo "Arrancando Nodo 2..."
icegridnode --Ice.Config=node2.config &
sleep 2

# 4. Desplegar la aplicación
echo "Desplegando SpotificeApp..."
icegridadmin --Ice.Config=registry.config -e "application add spotifice.xml"

echo "=== DESPLIEGUE COMPLETADO ==="
echo "Usa 'icegridadmin --Ice.Config=registry.config' para inspeccionar."
echo "Presiona CTRL+C para detener todo."

# Mantener el script vivo
wait
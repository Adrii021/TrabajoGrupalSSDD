#!/bin/bash

# 1. LIMPIEZA INICIAL
echo "[INFO] Limpiando entorno..."
# Matamos procesos viejos por si acaso se han quedado zombies
killall -9 icegridnode icegridregistry icepatch2server 2>/dev/null
rm -rf icegrid/db distrib
mkdir -p icegrid/db/registry icegrid/db/node1 icegrid/db/node2

# 2. PREPARAR PAQUETE (Nivel Intermedio con IcePatch2)
echo "[INFO] 📦 Generando paquete de distribución en 'distrib/'..."
mkdir -p distrib

# Copiamos código y datos
cp *.py *.ice *.json distrib/
cp -r media playlists distrib/ 2>/dev/null || true

# --- EL FIX DEFINITIVO PARA WINDOWS/WSL ---
# Forzamos la conversión a formato Unix DENTRO de la carpeta de destino.
# Esto elimina los errores de "import not found" o "word unexpected" causados por \r
echo "[INFO] 🔧 Sanear archivos (Quitando formato Windows CR)..."
sed -i 's/\r$//' distrib/*.py distrib/*.sh 2>/dev/null
chmod +x distrib/*.py
# -----------------------------------------

# Calculamos checksums para IcePatch2 con los archivos ya saneados
icepatch2calc distrib
echo "[OK] Archivos saneados y checksum calculado."

# 3. ARRANCAR INFRAESTRUCTURA
echo "[INFO] 🚀 Arrancando Registro IceGrid..."
icegridregistry --Ice.Config=registry.config &
sleep 2

echo "[INFO] 🚀 Arrancando Nodo 1 (IcePatch, Server1, Render2)..."
icegridnode --Ice.Config=node1.config &

echo "[INFO] 🚀 Arrancando Nodo 2 (Server2, Render1)..."
icegridnode --Ice.Config=node2.config &
sleep 3

# 4. DESPLEGAR APLICACIÓN
echo "[INFO] 📲 Desplegando descriptor XML..."
# Usamos credenciales por defecto (admin/admin) si se requieren
icegridadmin --Ice.Config=registry.config -u admin -p admin -e "application add spotifice.xml"

echo "=== ✅ DESPLIEGUE NIVEL INTERMEDIO COMPLETADO ==="
echo "La infraestructura está corriendo en segundo plano."
wait
